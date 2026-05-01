from __future__ import annotations

import io
import logging

log = logging.getLogger("edge_tts_service")

# gTTS usa lang='es' + tld para diferenciar acentos regionales
SPANISH_VOICES = [
    {"name": "com.ar", "display": "Español — Argentina (com.ar)"},
    {"name": "es",     "display": "Español — España (es)"},
    {"name": "com.mx", "display": "Español — México (com.mx)"},
    {"name": "com",    "display": "Español genérico (com)"},
]

DEFAULT_VOICE = "com.ar"


def generate_audio(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    from gtts import gTTS
    tts = gTTS(text=text, lang="es", tld=voice, slow=False)
    buf = io.BytesIO()
    tts.write_to_fp(buf)
    return buf.getvalue()


async def generate_audio_async(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    return generate_audio(text, voice)
