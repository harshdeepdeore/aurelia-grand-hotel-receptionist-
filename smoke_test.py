import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')
ELEVENLABS_VOICE_ID = os.getenv('ELEVENLABS_VOICE_ID')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')
TTS_MODEL = os.getenv('ELEVENLABS_TTS_MODEL', 'eleven_flash_v2_5')
OUTPUT_FORMAT = os.getenv('ELEVENLABS_TTS_OUTPUT_FORMAT', 'pcm_16000')

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

voices = requests.get(
    'https://api.elevenlabs.io/v1/voices',
    headers={'xi-api-key': ELEVENLABS_API_KEY},
    timeout=15,
)
voices.raise_for_status()
voice_ids = {v.get('voice_id') for v in voices.json().get('voices', [])}
if ELEVENLABS_VOICE_ID not in voice_ids:
    print('⚠️ Voice ID is not present in the voices returned by the API.')
else:
    print('✅ ElevenLabs voice access OK')

tts = requests.post(
    f'https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream'
    f'?output_format={OUTPUT_FORMAT}',
    headers={'xi-api-key': ELEVENLABS_API_KEY, 'Content-Type': 'application/json'},
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
tts.raise_for_status()
audio_path = Path('smoke_test_tts.pcm')
audio_path.write_bytes(tts.content)
print(f'✅ ElevenLabs TTS OK — wrote {len(tts.content):,} bytes to {audio_path}')

client = OpenAI(api_key=OPENAI_API_KEY)
result = client.chat.completions.create(
    model=OPENAI_MODEL,
    temperature=0,
    max_tokens=20,
    messages=[{'role': 'user', 'content': 'Reply with exactly: Aurelia test OK'}],
)
reply = (result.choices[0].message.content or '').strip()
print(f'✅ OpenAI LLM OK — response: {reply}')

print('\n🎉 Provider smoke test passed.')
print('Next: run `python exotel_bridge.py` and connect Exotel to the public WSS URL.')