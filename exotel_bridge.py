import asyncio
import audioop
import base64
import io
import json
import os
import http
import logging
import re
import sys
import time
import wave
from typing import List, Optional, Tuple

import requests
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed
from dotenv import load_dotenv
from openai import OpenAI

from prompt import SYSTEM_PROMPT

load_dotenv()

# ============================================================
# RENDER LOGGING
# ============================================================
# Render captures stdout/stderr. Force line-buffered output so
# every call-stage message appears immediately in Application Logs.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

def log(*args):
    print(*args, flush=True)

# Keep routine websocket probe failures (Render HEAD checks) out of
# the application logs. Real Exotel/WebSocket lifecycle messages below
# are still logged by our own handler.
WEBSOCKET_LOGGER = logging.getLogger("aurelia.websocket")
WEBSOCKET_LOGGER.setLevel(logging.CRITICAL)

# ============================================================
# ENVIRONMENT
# ============================================================

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2")
ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
ELEVENLABS_TTS_OUTPUT_FORMAT = os.getenv("ELEVENLABS_TTS_OUTPUT_FORMAT", "pcm_16000")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

PORT = int(os.getenv("PORT", os.getenv("BRIDGE_PORT", "5001")))

if not ELEVENLABS_API_KEY:
    raise RuntimeError("ELEVENLABS_API_KEY is missing")
if not ELEVENLABS_VOICE_ID:
    raise RuntimeError("ELEVENLABS_VOICE_ID is missing")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing")

# ============================================================
# AUDIO CONFIG
# ============================================================

ELEVENLABS_SAMPLE_RATE = 16000
DEFAULT_EXOTEL_SAMPLE_RATE = 16000
EXOTEL_OUTGOING_CHUNK_BYTES = 3200

# Caller turn detection
SILENCE_RMS_THRESHOLD = 700
SILENCE_FRAMES_TO_END_TURN = 25
MAX_BUFFER_BYTES = ELEVENLABS_SAMPLE_RATE * 2 * 20
MAX_CONTINUOUS_SPEAKING_SECONDS = 8.0
MIN_TURN_AUDIO_BYTES = 9600

# Barge-in
BARGE_IN_RMS_THRESHOLD = 1200
BARGE_IN_FRAMES_TO_TRIGGER = 8

# TTS buffering
PREBUFFER_MS = 800
REBUFFER_MS = 300

# Supported reply tags
SUPPORTED_TTS_LANGUAGES = {"hi-IN", "en-IN", "mr-IN"}
ELEVENLABS_LANGUAGE_CODES = {
    "hi-IN": "hi",
    "en-IN": "en",
    "mr-IN": "mr",
}
LANGUAGE_TAG_RE = re.compile(r"^\[(hi-IN|en-IN|mr-IN)\]\s*", re.IGNORECASE)

ALLOWED_SCRIPT_RE = re.compile(r"^[\u0900-\u097F\u0020-\u007E\s]*$")

openai_client = OpenAI(api_key=OPENAI_API_KEY)
http_session = requests.Session()

# ============================================================
# GREETING
# ============================================================

GREETING_TEXT = "Namaste! Aurelia Grand Hotel, Pune mein aapka swagat hai. Main Arjun bol raha hoon. Main aapki kis tarah madad kar sakta hoon?"
GREETING_LANGUAGE = "hi-IN"

# ============================================================
# AUDIO HELPERS
# ============================================================

def resample_pcm16(pcm_bytes: bytes, input_rate: int, output_rate: int) -> bytes:
    if not pcm_bytes:
        return b""
    if input_rate == output_rate:
        return pcm_bytes
    converted, _state = audioop.ratecv(
        pcm_bytes,
        2,
        1,
        input_rate,
        output_rate,
        None,
    )
    return converted


def calculate_buffer_chunks(sample_rate: int, target_ms: int) -> int:
    chunk_duration_ms = EXOTEL_OUTGOING_CHUNK_BYTES / (sample_rate * 2) * 1000.0
    return max(1, int((target_ms + chunk_duration_ms - 1) / chunk_duration_ms))

# ============================================================
# ELEVENLABS STT
# ============================================================

def elevenlabs_stt(pcm16_bytes: bytes, exotel_sample_rate: int) -> str:
    """Transcribe one completed caller turn with ElevenLabs Scribe v2."""

    pcm16_16k = resample_pcm16(
        pcm16_bytes,
        exotel_sample_rate,
        ELEVENLABS_SAMPLE_RATE,
    )

    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(ELEVENLABS_SAMPLE_RATE)
        wf.writeframes(pcm16_16k)
    wav_buf.seek(0)

    response = http_session.post(
        "https://api.elevenlabs.io/v1/speech-to-text",
        headers={"xi-api-key": ELEVENLABS_API_KEY},
        files={"file": ("audio.wav", wav_buf, "audio/wav")},
        data={"model_id": ELEVENLABS_STT_MODEL},
        timeout=20,
    )

    if response.status_code != 200:
        log("❌ ElevenLabs STT error:", response.status_code, response.text)

    response.raise_for_status()
    data = response.json()

    transcript = str(data.get("text", data.get("transcript", ""))).strip()

    # Prevent obvious unsupported-script garbage from reaching the LLM.
    if transcript and not ALLOWED_SCRIPT_RE.match(transcript):
        log("⚠️ Discarding unexpected transcript:", transcript)
        return ""

    return transcript

# ============================================================
# OPENAI LLM
# ============================================================

def ask_llm(history: List[dict], user_text: str) -> Tuple[str, str, str]:
    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + history
        + [{"role": "user", "content": user_text}]
    )

    response = openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.45,
        max_tokens=120,
        messages=messages,
    )

    raw_reply = (response.choices[0].message.content or "").strip()
    match = LANGUAGE_TAG_RE.match(raw_reply)

    if match:
        language_code = match.group(1).lower()
        reply_text = raw_reply[match.end():].strip()
    else:
        # Safe fallback for replies that forgot the tag.
        language_code = "hi-IN"
        reply_text = raw_reply

    if language_code not in SUPPORTED_TTS_LANGUAGES:
        language_code = "hi-IN"

    return reply_text, language_code, raw_reply

# ============================================================
# ELEVENLABS TTS STREAM
# ============================================================

def tts_stream_producer(
    text: str,
    language_code: str,
    exotel_sample_rate: int,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
):
    """Generate raw PCM from ElevenLabs and push Exotel-sized chunks."""

    pending = bytearray()
    resample_state = None

    try:
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/"
            f"{ELEVENLABS_VOICE_ID}/stream"
            f"?output_format={ELEVENLABS_TTS_OUTPUT_FORMAT}"
        )

        response = http_session.post(
            url,
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": ELEVENLABS_TTS_MODEL,
                "language_code": ELEVENLABS_LANGUAGE_CODES.get(language_code),
                "voice_settings": {
                    # Keep the delivery natural and consistent for phone calls.
                    "stability": 0.55,
                    "similarity_boost": 0.80,
                    "style": 0.0,
                    "use_speaker_boost": True,
                    "speed": 1.0,
                },
            },
            stream=True,
            timeout=30,
        )

        if response.status_code != 200:
            log("❌ ElevenLabs TTS error:", response.status_code, response.text)
            return

        for source_chunk in response.iter_content(chunk_size=4096):
            if not source_chunk:
                continue

            if exotel_sample_rate == ELEVENLABS_SAMPLE_RATE:
                converted = source_chunk
            else:
                converted, resample_state = audioop.ratecv(
                    source_chunk,
                    2,
                    1,
                    ELEVENLABS_SAMPLE_RATE,
                    exotel_sample_rate,
                    resample_state,
                )

            pending.extend(converted)

            while len(pending) >= EXOTEL_OUTGOING_CHUNK_BYTES:
                ready_chunk = bytes(pending[:EXOTEL_OUTGOING_CHUNK_BYTES])
                del pending[:EXOTEL_OUTGOING_CHUNK_BYTES]
                loop.call_soon_threadsafe(queue.put_nowait, ready_chunk)

        if pending:
            final_chunk = bytes(pending)
            remainder = len(final_chunk) % 320
            if remainder:
                final_chunk += b"\x00" * (320 - remainder)
            if len(final_chunk) < EXOTEL_OUTGOING_CHUNK_BYTES:
                final_chunk += b"\x00" * (EXOTEL_OUTGOING_CHUNK_BYTES - len(final_chunk))
            loop.call_soon_threadsafe(queue.put_nowait, final_chunk)

    except Exception as exc:
        log("❌ ElevenLabs TTS exception:", exc)
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)


async def stream_tts_and_send(
    ws,
    stream_sid: str,
    text: str,
    language_code: str,
    exotel_sample_rate: int,
    label: str = "Arjun",
):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    prebuffer_chunks = calculate_buffer_chunks(exotel_sample_rate, PREBUFFER_MS)
    rebuffer_chunks = calculate_buffer_chunks(exotel_sample_rate, REBUFFER_MS)
    started = time.time()
    first_audio_logged = False

    loop.run_in_executor(
        None,
        tts_stream_producer,
        text,
        language_code,
        exotel_sample_rate,
        loop,
        queue,
    )

    async def fetch_chunks(count: int):
        chunks = []
        for _ in range(count):
            chunk = await queue.get()
            chunks.append(chunk)
            if chunk is None:
                break
        return chunks

    buffer = await fetch_chunks(prebuffer_chunks)

    try:
        while True:
            if not buffer:
                buffer = await fetch_chunks(rebuffer_chunks)
                if not buffer:
                    break

            chunk = buffer.pop(0)
            if chunk is None:
                break

            payload = base64.b64encode(chunk).decode("ascii")
            await ws.send(
                json.dumps(
                    {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": payload},
                    }
                )
            )

            if not first_audio_logged:
                log(f"🔊 First {label} audio byte: {time.time() - started:.2f}s")
                first_audio_logged = True

            await asyncio.sleep(len(chunk) / (exotel_sample_rate * 2))

            while len(buffer) < prebuffer_chunks:
                try:
                    buffer.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

        log(f"🔊 {label} finished speaking in {time.time() - started:.2f}s")

    except asyncio.CancelledError:
        log(f"✋ {label} speech interrupted")
        raise
    except ConnectionClosed:
        log("⚠️ Caller disconnected during TTS")
    except Exception as exc:
        log("❌ TTS playback exception:", exc)

# ============================================================
# ONE CALLER TURN
# ============================================================

async def process_turn(
    history: List[dict],
    user_audio: bytes,
    exotel_sample_rate: int,
) -> Optional[Tuple[str, str]]:
    if not user_audio:
        return None

    log(f"🎙️ Processing caller turn | {len(user_audio)} audio bytes")
    stt_start = time.time()
    transcript = await asyncio.to_thread(
        elevenlabs_stt,
        user_audio,
        exotel_sample_rate,
    )
    log(f"🗣️ Caller: {transcript} | STT {time.time() - stt_start:.2f}s")

    if not transcript:
        return None

    log("🧠 Sending caller transcript to LLM...")
    llm_start = time.time()
    reply_text, language_code, raw_reply = await asyncio.to_thread(
        ask_llm,
        history,
        transcript,
    )

    history.append({"role": "user", "content": transcript})
    history.append({"role": "assistant", "content": raw_reply})

    log(f"🤖 Arjun [{language_code}]: {reply_text} | LLM {time.time() - llm_start:.2f}s")
    return reply_text, language_code

# ============================================================
# CALL HANDLER
# ============================================================

async def handle_call(ws):
    log("🔌 New Exotel connection")

    stream_sid: Optional[str] = None
    exotel_sample_rate = DEFAULT_EXOTEL_SAMPLE_RATE
    history: List[dict] = []
    audio_buffer = bytearray()
    silence_count = 0
    speaking = False
    speaking_started_at: Optional[float] = None
    barge_in_count = 0
    turn_task: Optional[asyncio.Task] = None
    tts_task: Optional[asyncio.Task] = None

    async def run_turn(user_audio: bytes):
        nonlocal tts_task
        try:
            result = await process_turn(history, user_audio, exotel_sample_rate)
            if result is None:
                return
            reply_text, language_code = result
            if tts_task is not None and not tts_task.done():
                log("⚠️ Existing TTS still active; skipping overlap")
                return
            tts_task = asyncio.create_task(
                stream_tts_and_send(
                    ws,
                    stream_sid,
                    reply_text,
                    language_code,
                    exotel_sample_rate,
                    label="Arjun",
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log("❌ Turn processing error:", exc)

    def start_turn():
        nonlocal turn_task, speaking, silence_count, speaking_started_at

        speaking = False
        silence_count = 0
        speaking_started_at = None

        if len(audio_buffer) < MIN_TURN_AUDIO_BYTES:
            audio_buffer.clear()
            return

        if len(audio_buffer) > MAX_BUFFER_BYTES:
            log("⚠️ Caller buffer exceeded limit; clearing")
            audio_buffer.clear()
            return

        if turn_task is not None and not turn_task.done():
            log("⚠️ Previous turn still processing; dropping overlapping turn")
            audio_buffer.clear()
            return

        user_audio = bytes(audio_buffer)
        audio_buffer.clear()
        turn_task = asyncio.create_task(run_turn(user_audio))

    try:
        async for raw_message in ws:
            message = json.loads(raw_message)
            event = message.get("event")

            if event == "connected":
                continue

            if event == "start":
                start_data = message.get("start", {})
                stream_sid = start_data.get("stream_sid")
                media_format = start_data.get("media_format", {})
                try:
                    exotel_sample_rate = int(media_format.get("sample_rate", DEFAULT_EXOTEL_SAMPLE_RATE))
                except (TypeError, ValueError):
                    exotel_sample_rate = DEFAULT_EXOTEL_SAMPLE_RATE

                log(
                    "▶️ Stream started | "
                    f"sid={stream_sid} | "
                    f"sample_rate={exotel_sample_rate} | "
                    f"encoding={media_format.get('encoding', 'unknown')}"
                )

                history.append({
                    "role": "assistant",
                    "content": f"[{GREETING_LANGUAGE}] {GREETING_TEXT}",
                })

                tts_task = asyncio.create_task(
                    stream_tts_and_send(
                        ws,
                        stream_sid,
                        GREETING_TEXT,
                        GREETING_LANGUAGE,
                        exotel_sample_rate,
                        label="Greeting",
                    )
                )
                continue

            if event == "media":
                frame = base64.b64decode(message["media"]["payload"])
                rms = audioop.rms(frame, 2)

                # Barge-in while Arjun is speaking.
                if tts_task is not None and not tts_task.done():
                    if rms > BARGE_IN_RMS_THRESHOLD:
                        barge_in_count += 1
                        if barge_in_count >= BARGE_IN_FRAMES_TO_TRIGGER:
                            log("✋ Barge-in detected — cancelling TTS")
                            tts_task.cancel()
                            tts_task = None
                            barge_in_count = 0
                            speaking = True
                            silence_count = 0
                            speaking_started_at = time.time()
                            audio_buffer.clear()
                            audio_buffer.extend(frame)
                    else:
                        barge_in_count = 0
                    continue

                if tts_task is not None and tts_task.done():
                    tts_task = None
                    barge_in_count = 0

                if rms > SILENCE_RMS_THRESHOLD:
                    if not speaking:
                        speaking_started_at = time.time()
                    speaking = True
                    silence_count = 0
                    audio_buffer.extend(frame)
                elif speaking:
                    silence_count += 1
                    audio_buffer.extend(frame)
                    if silence_count > SILENCE_FRAMES_TO_END_TURN:
                        start_turn()
                        continue

                if (
                    speaking
                    and speaking_started_at is not None
                    and time.time() - speaking_started_at > MAX_CONTINUOUS_SPEAKING_SECONDS
                ):
                    log("⏱️ Forcing caller turn end")
                    start_turn()
                continue

            if event == "dtmf":
                log("DTMF:", message.get("dtmf"))
                continue

            if event == "stop":
                log("⏹️ Call ended")
                break

    except (
        ConnectionClosed,
        BrokenPipeError,
        ConnectionResetError,
    ) as exc:
        log("⚠️ Connection dropped:", exc)
    except Exception as exc:
        log("❌ Call handler error:", exc)
    finally:
        if tts_task is not None and not tts_task.done():
            tts_task.cancel()
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()

# ============================================================
# HTTP HEALTH CHECK
# ============================================================

def health_check(connection, request):
    """
    Render and other infrastructure may send normal HTTP HEAD/GET
    probes to the web service. Keep those requests away from the
    WebSocket handler while allowing real WebSocket upgrades through.
    """
    upgrade = request.headers.get("Upgrade", "").lower()
    if upgrade != "websocket":
        return connection.respond(
            http.HTTPStatus.OK,
            "Aurelia Grand Hotel voice bridge is live.\\n",
        )
    return None


# ============================================================
# SERVER
# ============================================================


async def main():
    async with serve(
        handle_call,
        "0.0.0.0",
        PORT,
        process_request=health_check,
        ping_interval=20,
        ping_timeout=20,
        max_size=2**20,
        logger=WEBSOCKET_LOGGER,
    ):
        log(f"✅ Aurelia Grand Hotel Exotel bridge listening on port {PORT}")
        log(f"🤖 LLM: {OPENAI_MODEL}")
        log(f"📝 ElevenLabs STT: {ELEVENLABS_STT_MODEL}")
        log(f"🔊 ElevenLabs TTS: {ELEVENLABS_TTS_MODEL} / {ELEVENLABS_TTS_OUTPUT_FORMAT}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
