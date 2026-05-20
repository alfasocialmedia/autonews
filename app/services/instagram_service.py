"""
Instagram Graph API (Instagram Login): publicar imágenes, renovar token, probar conexión.
Flujo: crear container (/{ig_user_id}/media) → publicar (/{ig_user_id}/media_publish).
Usa graph.instagram.com (Instagram API with Instagram Login).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.instagram.com"
TIMEOUT = 30


def test_connection(ig_user_id: str, access_token: str) -> dict:
    """Verifica que el token y el ID de cuenta sean válidos."""
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
    """Publica una imagen en Instagram en dos pasos (container → publish)."""
    try:
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

        time.sleep(3)

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
    """Renueva un long-lived token de Instagram Login (válido ~60 días).
    No necesita app_id ni app_secret — solo el token actual.
    Los parámetros app_id/app_secret se mantienen por compatibilidad de firma.
    """
    try:
        r = requests.get(
            f"{GRAPH_BASE}/refresh_access_token",
            params={
                "grant_type": "ig_refresh_token",
                "access_token": current_token,
            },
            timeout=TIMEOUT,
        )
        data = r.json()
        if "error" in data:
            return {"ok": False, "error": data["error"].get("message", "Error renovando token")}
        expires_in = data.get("expires_in", 0)
        return {
            "ok": True,
            "access_token": data.get("access_token", current_token),
            "expires_in": expires_in,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def token_expires_at(app_id: str, app_secret: str, token: str) -> datetime | None:
    """Consulta cuándo expira el token usando el endpoint de Instagram Login."""
    try:
        # Intentar refrescar para obtener expires_in actualizado
        r = requests.get(
            f"{GRAPH_BASE}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
            timeout=TIMEOUT,
        )
        data = r.json()
        expires_in = data.get("expires_in")
        if expires_in:
            return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        return None
    except Exception:
        return None
