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


async def _generate_async(text: str, voice: str) -> bytes:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    audio = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return audio


def generate_audio(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_generate_async(text, voice))
    finally:
        loop.close()
