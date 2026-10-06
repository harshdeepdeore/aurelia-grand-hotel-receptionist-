import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY', '').strip()
ELEVENLABS_VOICE_ID = os.getenv('ELEVENLABS_VOICE_ID', '').strip()
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '').strip()
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini').strip()
TTS_MODEL = os.getenv('ELEVENLABS_TTS_MODEL', 'eleven_flash_v2_5').strip()
OUTPUT_FORMAT = os.getenv('ELEVENLABS_TTS_OUTPUT_FORMAT', 'pcm_16000').strip()

missing = []
for name, value in (
    ('ELEVENLABS_API_KEY', ELEVENLABS_API_KEY),
    ('ELEVENLABS_VOICE_ID', ELEVENLABS_VOICE_ID),
    ('OPENAI_API_KEY', OPENAI_API_KEY),
):
    if not value or value.startswith('your_'):
        missing.append(name)

if missing:
    print('❌ Missing environment variables:', ', '.join(missing))
    print('Copy .env.example to .env and add the real keys/voice ID.')
    sys.exit(1)

print('✅ Environment variables loaded')

# Test the exact endpoint our bridge uses.
tts_url = (
    f'https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream'
    f'?output_format={OUTPUT_FORMAT}'
)
tts = requests.post(
    tts_url,
    headers={
        'xi-api-key': ELEVENLABS_API_KEY,
        'Content-Type': 'application/json',
    },
    json={
        'text': 'Namaste. Aurelia Grand Hotel mein aapka swagat hai.',
        'model_id': TTS_MODEL,
        'voice_settings': {
            'stability': 0.48,
            'similarity_boost': 0.82,
            'style': 0.18,
            'use_speaker_boost': True,
            'speed': 1.05,
        },
    },
    timeout=30,
)

if tts.status_code != 200:
    print(f'❌ ElevenLabs TTS error: HTTP {tts.status_code}')
    print(tts.text)
    if tts.status_code == 401:
        print('Hint: check that the key is valid, unexpired, and copied without extra quotes/spaces.')
    if tts.status_code == 403:
        print('Hint: edit the API key and make sure Text to Speech has Access.')
    sys.exit(1)

audio_path = Path('smoke_test_tts.pcm')
audio_path.write_bytes(tts.content)
print(f'✅ ElevenLabs TTS OK — wrote {len(tts.content):,} bytes to {audio_path}')

client = OpenAI(api_key=OPENAI_API_KEY)
try:
    result = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        max_tokens=20,
        messages=[{'role': 'user', 'content': 'Reply with exactly: Aurelia test OK'}],
    )
except Exception as exc:
    print('❌ OpenAI LLM error:', exc)
    sys.exit(1)

reply = (result.choices[0].message.content or '').strip()
print(f'✅ OpenAI LLM OK — response: {reply}')

print('\n🎉 Provider smoke test passed.')
print('Next: run `python exotel_bridge.py` and connect Exotel to the public WSS URL.')