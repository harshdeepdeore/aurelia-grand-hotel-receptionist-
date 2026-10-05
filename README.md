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

## Local setup and smoke test

Create a local environment file:

cp .env.example .env

Then set the real values for ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, OPENAI_API_KEY, and the model settings shown in .env.example.

Do not commit .env.

Install and run the provider smoke test:

pip install -r requirements.txt
python smoke_test.py

The smoke test checks ElevenLabs voice access, generates a very short PCM sample, and checks the OpenAI LLM connection. It does not place a phone call.

Then start the bridge:

python exotel_bridge.py

For a real Exotel test, the bridge must be reachable over a public secure WebSocket URL (wss://...).

## Current ElevenLabs choices

The bridge uses Scribe v2 for completed-turn transcription and Flash v2.5 for low-latency multilingual TTS. ElevenLabs currently documents Scribe v2 for batch transcription and Flash v2.5 for low-latency conversational TTS; raw PCM 16 kHz is supported for audio pipelines.