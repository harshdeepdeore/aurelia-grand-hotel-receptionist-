# Aurelia Grand Hotel — AI Voice Receptionist

Custom Exotel voice-agent bridge for Aurelia Grand Hotel, Pune.

## Architecture

Exotel → Python bridge → ElevenLabs STT → OpenAI LLM → ElevenLabs TTS → Exotel

The project is intentionally separate from the AITEK/Ria `ai-receptionist` repository.

## Why this repo exists

This version removes the Sarvam-specific STT/TTS implementation and keeps the Exotel audio/turn-handling architecture separate for the hotel project.

## Before running

1. Copy `.env.example` to `.env`.
2. Add your ElevenLabs API key.
3. Add the ElevenLabs voice ID you want to use for the hotel receptionist.
4. Add your OpenAI API key.
5. Replace `hotel_knowledge.txt` with the approved Aurelia Grand Hotel knowledge base.
6. Install dependencies with `pip install -r requirements.txt`.
7. Start the bridge with `python exotel_bridge.py`.
8. Point the Exotel WebSocket/voicebot configuration to the public `wss://` endpoint for this service.

## Important

This repository does not contain any real API keys.

The code currently uses turn-based STT with ElevenLabs Scribe v2, then streams ElevenLabs TTS as raw PCM. This is intentionally simple and cost-conscious. A later version can move STT to ElevenLabs Scribe v2 Realtime if lower turn latency is required.

## Main files

- `exotel_bridge.py` — Exotel WebSocket, audio processing, STT, LLM and TTS.
- `prompt.py` — hotel receptionist behaviour and language rules.
- `hotel_knowledge.txt` — verified hotel facts used by the prompt.
- `.env.example` — environment variable template.
- `Procfile` — deployment command.
- `runtime.txt` — Python version.

## Production note

Do not claim a booking, payment, reservation change, message delivery, or database lookup unless a real integration/tool has been added and completed the action.
