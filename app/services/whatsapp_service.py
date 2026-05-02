from __future__ import annotations

import logging
import mimetypes
import re

import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("whatsapp_service")

TIMEOUT = 60
VERIFY_SSL = False  # servidor a servidor en el mismo VPS


def _headers(api_key: str) -> dict:
    return {"apikey": api_key, "Content-Type": "application/json"}


# ── Instancia ──────────────────────────────────────────────────────────────────

def create_instance(url: str, api_key: str, instance_name: str) -> dict:
    try:
        r = requests.post(
            f"{url}/instance/create",
            headers=_headers(api_key),
            json={
                "instanceName": instance_name,
                "qrcode": True,
                "integration": "WHATSAPP-BAILEYS",
            },
            timeout=TIMEOUT, verify=VERIFY_SSL,
        )
        if r.status_code == 400:
            # Instancia ya existe — no es un error
            return {"instanceName": instance_name, "already_exists": True}
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 400:
            return {"instanceName": instance_name, "already_exists": True}
        raise


def get_qr(url: str, api_key: str, instance_name: str) -> dict:
    """Devuelve {'base64': '...', 'code': '...'} o {'error': '...'}."""
    try:
        r = requests.get(
            f"{url}/instance/connect/{instance_name}",
            headers=_headers(api_key),
            timeout=TIMEOUT, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        data = r.json()
        # Evolution API v2 puede devolver el QR en distintos campos
        base64 = (
            data.get("base64")
            or data.get("qrcode", {}).get("base64")
            or data.get("qr")
            or ""
        )
        return {"base64": base64, "code": data.get("code", "")}
    except Exception as exc:
        return {"error": str(exc)}


def get_status(url: str, api_key: str, instance_name: str) -> dict:
    """Devuelve {'state': 'open'|'close'|'connecting', ...}."""
    try:
        r = requests.get(
            f"{url}/instance/connectionState/{instance_name}",
            headers=_headers(api_key),
            timeout=TIMEOUT, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        data = r.json()
        state = (
            data.get("instance", {}).get("state")
            or data.get("state")
            or "unknown"
        )
        return {"state": state}
    except Exception as exc:
        return {"state": "error", "error": str(exc)}


def set_webhook(url: str, api_key: str, instance_name: str, webhook_url: str) -> bool:
    try:
        r = requests.post(
            f"{url}/webhook/set/{instance_name}",
            headers=_headers(api_key),
            json={
                "webhook": {
                    "enabled": True,
                    "url": webhook_url,
                    "webhookByEvents": False,
                    "webhookBase64": False,
                    "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
                }
            },
            timeout=TIMEOUT, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("set_webhook error: %s", exc)
        return False


# ── Grupos ─────────────────────────────────────────────────────────────────────

def fetch_groups(url: str, api_key: str, instance_name: str) -> list[dict]:
    """Devuelve lista de {'id': jid, 'subject': name}."""
    try:
        r = requests.get(
            f"{url}/group/fetchAllGroups/{instance_name}",
            headers=_headers(api_key),
            params={"getParticipants": "false"},
            timeout=TIMEOUT, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return [{"id": g.get("id", ""), "subject": g.get("subject", "")} for g in data]
        return []
    except Exception as exc:
        log.warning("fetch_groups error: %s", exc)
        return []


# ── Envío de mensajes ──────────────────────────────────────────────────────────

def send_text(url: str, api_key: str, instance_name: str, jid: str, text: str) -> bool:
    try:
        r = requests.post(
            f"{url}/message/sendText/{instance_name}",
            headers=_headers(api_key),
            json={"number": jid, "text": text},
            timeout=TIMEOUT, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("send_text to %s error: %s", jid, exc)
        return False


def send_image(
    url: str, api_key: str, instance_name: str,
    jid: str, image_url: str, caption: str = "",
) -> bool:
    try:
        r = requests.post(
            f"{url}/message/sendMedia/{instance_name}",
            headers=_headers(api_key),
            json={
                "number": jid,
                "mediatype": "image",
                "media": image_url,
                "caption": caption,
            },
            timeout=TIMEOUT, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("send_image to %s error: %s", jid, exc)
        return False


# ── Descarga de media desde WA ─────────────────────────────────────────────────

def get_media_base64(url: str, api_key: str, instance_name: str, raw_data: dict) -> tuple[bytes, str] | None:
    """
    Descarga media de un mensaje usando la API de Evolution (base64).
    raw_data es el objeto completo del mensaje (incluye key + message).
    Devuelve (bytes, mimetype) o None si falla.
    """
    import base64 as b64lib
    try:
        r = requests.post(
            f"{url}/chat/getBase64FromMediaMessage/{instance_name}",
            headers=_headers(api_key),
            json={"message": raw_data},
            timeout=60,
            verify=VERIFY_SSL,
        )
        r.raise_for_status()
        data = r.json()
        encoded = data.get("base64", "")
        if not encoded:
            return None
        raw = b64lib.b64decode(encoded)
        mimetype = (data.get("mimetype") or "application/octet-stream").split(";")[0].strip()
        return raw, mimetype
    except Exception as exc:
        log.warning("get_media_base64 error: %s", exc)
        return None


def download_media(url: str, api_key: str, instance_name: str, message: dict) -> tuple[bytes, str, str] | None:
    """Descarga media por URL directa (fallback). Devuelve (bytes, filename, mimetype) o None."""
    try:
        media_url = (
            message.get("mediaUrl")
            or message.get("url")
            or message.get("directPath")
        )
        if not media_url:
            return None

        r = requests.get(media_url, headers=_headers(api_key), timeout=60)
        r.raise_for_status()

        mimetype = r.headers.get("content-type", "application/octet-stream").split(";")[0]
        ext = mimetypes.guess_extension(mimetype) or ".bin"
        filename = f"wa_media{ext}"
        return r.content, filename, mimetype
    except Exception as exc:
        log.warning("download_media error: %s", exc)
        return None


# ── Parseo de mensajes entrantes ───────────────────────────────────────────────

def parse_incoming(payload: dict) -> dict | None:
    """
    Convierte el payload de Evolution API en un dict normalizado:
    {
        'from': '5491112345678',   # número limpio
        'jid': '5491112345678@s.whatsapp.net',
        'type': 'text' | 'image' | 'video' | 'audio' | 'document',
        'text': '...',
        'media': {...},            # sub-dict del mensaje original si tiene media
        'is_group': bool,
        'group_jid': '...' | None,
    }
    """
    try:
        data = payload.get("data", {})
        key = data.get("key", {})

        # Ignorar mensajes enviados por nuestra propia instancia
        if key.get("fromMe"):
            return None

        remote_jid = key.get("remoteJid", "")
        is_group = remote_jid.endswith("@g.us")
        participant = key.get("participant") or data.get("participant") or remote_jid

        # Número limpio
        sender_jid = participant if participant else remote_jid
        sender_num = re.sub(r"[^0-9]", "", sender_jid.split("@")[0])

        msg = data.get("message", {})
        msg_type = data.get("messageType", "")

        text = ""
        media = None

        if msg_type in ("conversation", "extendedTextMessage"):
            text = msg.get("conversation") or msg.get("extendedTextMessage", {}).get("text", "")
        elif msg_type == "imageMessage":
            media = msg.get("imageMessage", {})
            text = media.get("caption", "")
        elif msg_type == "videoMessage":
            media = msg.get("videoMessage", {})
            text = media.get("caption", "")
        elif msg_type == "documentMessage":
            media = msg.get("documentMessage", {})
            text = media.get("caption", "")
        elif msg_type == "audioMessage":
            media = msg.get("audioMessage", {})
        else:
            return None

        return {
            "from": sender_num,
            "jid": sender_jid,
            "type": _simplify_type(msg_type),
            "text": text,
            "media": media,
            "is_group": is_group,
            "group_jid": remote_jid if is_group else None,
            "raw_message": msg,
            "_raw_data": data,
        }
    except Exception as exc:
        log.warning("parse_incoming error: %s", exc)
        return None


def _simplify_type(msg_type: str) -> str:
    if "image" in msg_type.lower():
        return "image"
    if "video" in msg_type.lower():
        return "video"
    if "audio" in msg_type.lower():
        return "audio"
    if "document" in msg_type.lower():
        return "document"
    return "text"
