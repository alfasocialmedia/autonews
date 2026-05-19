"""
Instagram Graph API: publicar imágenes, renovar token, probar conexión.
Flujo: crear container (/{ig_user_id}/media) → publicar (/{ig_user_id}/media_publish).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v19.0"
TIMEOUT = 30


def test_connection(ig_user_id: str, access_token: str) -> dict:
    """Verifica que el token y el ID de cuenta sean válidos.
    Retorna {"ok": True, "username": "...", "name": "..."} o {"ok": False, "error": "..."}.
    """
    try:
        r = requests.get(
            f"{GRAPH_BASE}/{ig_user_id}",
            params={"fields": "name,username", "access_token": access_token},
            timeout=TIMEOUT,
        )
        data = r.json()
        if "error" in data:
            return {"ok": False, "error": data["error"].get("message", "Error desconocido")}
        return {"ok": True, "username": data.get("username", ""), "name": data.get("name", "")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def publish_image(ig_user_id: str, access_token: str, image_url: str, caption: str) -> dict:
    """Publica una imagen en Instagram en dos pasos.
    Retorna {"ok": True, "media_id": "..."} o {"ok": False, "error": "..."}.
    """
    try:
        # Paso 1: crear container
        r = requests.post(
            f"{GRAPH_BASE}/{ig_user_id}/media",
            data={
                "image_url": image_url,
                "caption": caption,
                "access_token": access_token,
            },
            timeout=TIMEOUT,
        )
        data = r.json()
        if "error" in data:
            return {"ok": False, "error": data["error"].get("message", "Error creando container")}

        creation_id = data.get("id")
        if not creation_id:
            return {"ok": False, "error": "No se recibió creation_id del container"}

        # Espera breve recomendada por Meta antes de publicar
        time.sleep(3)

        # Paso 2: publicar container
        r2 = requests.post(
            f"{GRAPH_BASE}/{ig_user_id}/media_publish",
            data={"creation_id": creation_id, "access_token": access_token},
            timeout=TIMEOUT,
        )
        data2 = r2.json()
        if "error" in data2:
            return {"ok": False, "error": data2["error"].get("message", "Error publicando container")}

        return {"ok": True, "media_id": data2.get("id", "")}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def refresh_token(app_id: str, app_secret: str, current_token: str) -> dict:
    """Renueva un long-lived token (válido ~60 días).
    Retorna {"ok": True, "access_token": "...", "expires_in": N} o {"ok": False, "error": "..."}.
    """
    try:
        r = requests.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": current_token,
            },
            timeout=TIMEOUT,
        )
        data = r.json()
        if "error" in data:
            return {"ok": False, "error": data["error"].get("message", "Error renovando token")}
        return {
            "ok": True,
            "access_token": data.get("access_token", ""),
            "expires_in": data.get("expires_in", 0),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def token_expires_at(app_id: str, app_secret: str, token: str) -> datetime | None:
    """Consulta cuándo expira el token. Retorna datetime UTC o None si falla."""
    try:
        r = requests.get(
            f"{GRAPH_BASE}/debug_token",
            params={
                "input_token": token,
                "access_token": f"{app_id}|{app_secret}",
            },
            timeout=TIMEOUT,
        )
        data = r.json().get("data", {})
        exp = data.get("expires_at")
        if exp:
            return datetime.fromtimestamp(exp, tz=timezone.utc)
        return None
    except Exception:
        return None
