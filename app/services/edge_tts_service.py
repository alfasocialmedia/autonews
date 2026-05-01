from __future__ import annotations

import io
import logging

log = logging.getLogger("edge_tts_service")

SPANISH_VOICES = [
    {"name": "es",    "display": "Español genérico"},
    {"name": "es-AR", "display": "Español — Argentina"},
    {"name": "es-ES", "display": "Español — España"},
    {"name": "es-MX", "display": "Español — México"},
    {"name": "es-CO", "display": "Español — Colombia"},
    {"name": "es-CL", "display": "Español — Chile"},
    {"name": "es-US", "display": "Español — EEUU/Latino"},
]

DEFAULT_VOICE = "es-AR"


def generate_audio(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    from gtts import gTTS
    tts = gTTS(text=text, lang=voice, slow=False)
    buf = io.BytesIO()
    tts.write_to_fp(buf)
    return buf.getvalue()


async def generate_audio_async(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    return generate_audio(text, voice)
