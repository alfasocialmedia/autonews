from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("edge_tts_service")

SPANISH_VOICES = [
    {"name": "es-AR-TomasNeural",   "display": "Tomás — Argentina, masculino"},
    {"name": "es-AR-ElenaNeural",   "display": "Elena — Argentina, femenino"},
    {"name": "es-ES-AlvaroNeural",  "display": "Álvaro — España, masculino"},
    {"name": "es-ES-ElviraNeural",  "display": "Elvira — España, femenino"},
    {"name": "es-MX-JorgeNeural",   "display": "Jorge — México, masculino"},
    {"name": "es-MX-DaliaNeural",   "display": "Dalia — México, femenino"},
    {"name": "es-CO-GonzaloNeural", "display": "Gonzalo — Colombia, masculino"},
    {"name": "es-CO-SalomeNeural",  "display": "Salomé — Colombia, femenino"},
    {"name": "es-US-AlonsoNeural",  "display": "Alonso — EEUU/Latino, masculino"},
    {"name": "es-US-PalomaNeural",  "display": "Paloma — EEUU/Latino, femenino"},
]

DEFAULT_VOICE = "es-AR-TomasNeural"


async def generate_audio_async(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Versión async — usar en contextos async (FastAPI routes)."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    audio = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return audio


def generate_audio(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Versión sync — usar en hilos de fondo (worker). No llamar desde async."""
    return asyncio.run(generate_audio_async(text, voice))
