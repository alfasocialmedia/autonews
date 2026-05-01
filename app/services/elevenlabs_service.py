from __future__ import annotations

import html
import logging
import re

import httpx

log = logging.getLogger("elevenlabs_service")

_API_BASE = "https://api.elevenlabs.io/v1"


def strip_html(html_content: str) -> str:
    """Elimina tags HTML, entidades y marcadores de imagen inline, devuelve texto plano para TTS."""
    text = re.sub(r"<[^>]+>", " ", html_content)
    text = html.unescape(text)
    text = re.sub(r"\[image:[^\]]+\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def generate_audio(
    text: str,
    api_key: str,
    voice_id: str,
    model_id: str = "eleven_multilingual_v2",
) -> bytes:
    """Llama a la API TTS de ElevenLabs y devuelve bytes MP3."""
    url = f"{_API_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }
    with httpx.Client(timeout=120) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
    return resp.content


def test_connection(api_key: str) -> tuple[bool, str]:
    """Verifica la API key y lista las voces disponibles."""
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{_API_BASE}/voices",
                headers={"xi-api-key": api_key},
            )
        if resp.status_code == 200:
            voices = resp.json().get("voices", [])
            names = [v["name"] for v in voices[:6]]
            return True, f"Conexión OK — {len(voices)} voces disponibles: {', '.join(names)}"
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        return False, str(exc)


def list_voices(api_key: str) -> list[dict]:
    """Devuelve la lista de voces disponibles en la cuenta: [{voice_id, name, category, labels}]."""
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{_API_BASE}/voices",
            headers={"xi-api-key": api_key},
        )
        resp.raise_for_status()
    voices = resp.json().get("voices", [])
    return [
        {
            "voice_id": v["voice_id"],
            "name": v["name"],
            "category": v.get("category", ""),
            "language": v.get("labels", {}).get("language", ""),
            "accent": v.get("labels", {}).get("accent", ""),
            "gender": v.get("labels", {}).get("gender", ""),
        }
        for v in voices
    ]
