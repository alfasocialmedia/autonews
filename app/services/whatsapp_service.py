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
        if r.status_code == 403:
            # Puede significar: instancia ya conectada, o API key incorrecta
            try:
                body = r.json()
                body_str = str(body).lower()
            except Exception:
                body_str = r.text.lower()
            # Si parece que la instancia ya existe/está conectada, tratar como éxito
            if any(w in body_str for w in ("already", "exists", "connected", "conflict")):
                return {"instanceName": instance_name, "already_exists": True}
            # De lo contrario es un error de autenticación
            raise Exception(
                "API key incorrecta o sin permisos. "
                "Verificá que el API key coincida con AUTHENTICATION_API_KEY en tu Evolution API. "
                f"Respuesta: {r.text[:300]}"
            )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (400, 403):
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


_WEBHOOK_EVENTS = [
    "MESSAGES_UPSERT",
    "MESSAGES_UPDATE",
    "CONNECTION_UPDATE",
    "CHATS_UPSERT",
    "CHATS_UPDATE",
]


def set_webhook(url: str, api_key: str, instance_name: str, webhook_url: str) -> tuple[bool, str]:
    """Devuelve (ok, mensaje_error_o_exito).
    Intenta primero el formato v2 con wrapper 'webhook', luego sin wrapper (v1/v2 alternativo)."""
    endpoint = f"{url}/webhook/set/{instance_name}"
    headers = _headers(api_key)

    bodies = [
        # Formato v2 con wrapper
        {"webhook": {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": False,
            "webhookBase64": False,
            "events": _WEBHOOK_EVENTS,
        }},
        # Formato v1/alternativo sin wrapper
        {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": False,
            "webhookBase64": False,
            "events": _WEBHOOK_EVENTS,
        },
    ]

    last_error = ""
    for body in bodies:
        try:
            r = requests.post(endpoint, headers=headers, json=body, timeout=TIMEOUT, verify=VERIFY_SSL)
            if r.ok:
                log.info("set_webhook OK para %s", instance_name)
                return True, ""
            last_error = f"HTTP {r.status_code}: {r.text[:300]}"
            log.warning("set_webhook intento fallido: %s", last_error)
        except Exception as exc:
            last_error = str(exc)
            log.warning("set_webhook error: %s", exc)

    return False, last_error


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


def _extract_newsletter_items(raw) -> list[dict]:
    """Normaliza una lista o dict de la API a [{id, subject}] filtrando solo @newsletter JIDs."""
    rows = raw if isinstance(raw, list) else (
        raw.get("newsletters") or raw.get("channels") or raw.get("data") or []
    )
    items = []
    for n in rows:
        if not isinstance(n, dict):
            continue
        jid = n.get("id") or n.get("jid") or ""
        if not jid:
            continue
        name = (n.get("name") or n.get("subject") or n.get("title") or
                (n.get("newsletter", {}) or {}).get("name") or jid)
        items.append({"id": jid, "subject": name})
    return items


def fetch_newsletters(url: str, api_key: str, instance_name: str) -> tuple[list[dict], str]:
    """Intenta varios endpoints para obtener canales (newsletters) de la instancia."""
    hdrs = _headers(api_key)

    # 1. Endpoint nativo Evolution API v2
    for path in (
        f"/newsletter/findAll/{instance_name}",
        f"/newsletter/find/{instance_name}",
        f"/channel/findAll/{instance_name}",
    ):
        try:
            r = requests.get(f"{url}{path}", headers=hdrs, timeout=TIMEOUT, verify=VERIFY_SSL)
            if r.status_code in (404, 405, 403):
                continue
            r.raise_for_status()
            data = r.json()
            items = _extract_newsletter_items(data)
            if items:
                log.info("fetch_newsletters via %s: %d canal(es)", path, len(items))
                return items, "ok"
        except Exception as exc:
            log.debug("fetch_newsletters %s error: %s", path, exc)

    # 2. Filtrar @newsletter en la lista de grupos (algunas versiones los incluyen)
    try:
        r = requests.get(
            f"{url}/group/fetchAllGroups/{instance_name}",
            headers=hdrs,
            params={"getParticipants": "false"},
            timeout=TIMEOUT, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        data = r.json()
        groups = data if isinstance(data, list) else []
        items = [
            {"id": g.get("id", ""), "subject": g.get("subject") or g.get("name") or g.get("id", "")}
            for g in groups
            if isinstance(g.get("id"), str) and g["id"].endswith("@newsletter")
        ]
        if items:
            log.info("fetch_newsletters via groups: %d canal(es)", len(items))
            return items, "ok"
    except Exception as exc:
        log.debug("fetch_newsletters groups fallback error: %s", exc)

    # 3. Filtrar @newsletter en la lista de chats
    try:
        r = requests.get(
            f"{url}/chat/findChats/{instance_name}",
            headers=hdrs, timeout=TIMEOUT, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        data = r.json()
        chats = data if isinstance(data, list) else data.get("chats") or []
        items = [
            {"id": c.get("id", ""), "subject": c.get("name") or c.get("subject") or c.get("id", "")}
            for c in chats
            if isinstance(c.get("id"), str) and c["id"].endswith("@newsletter")
        ]
        log.info("fetch_newsletters via chats: %d canal(es)", len(items))
        return items, "ok"
    except Exception as exc:
        log.debug("fetch_newsletters chats fallback error: %s", exc)

    return [], "not_supported"


import re as _re_wa

_WA_CHANNEL_URL_RE = _re_wa.compile(
    r'(?:https?://)?(?:www\.)?whatsapp\.com/channel/([A-Za-z0-9_-]+)', _re_wa.IGNORECASE
)


def _parse_channel_input(raw: str) -> tuple[str, str]:
    """
    Acepta cualquiera de estos formatos y devuelve (tipo, valor_limpio):
      - JID:        '120363XXXXXXXXXX@newsletter'  → ('jid', '120363...@newsletter')
      - URL:        'https://whatsapp.com/channel/0029Vb3g6PO...' → ('invite', '0029Vb3g6PO...')
      - Invite code: '0029Vb3g6PO...'                             → ('invite', '0029Vb3g6PO...')
    """
    raw = raw.strip()
    m = _WA_CHANNEL_URL_RE.search(raw)
    if m:
        return "invite", m.group(1)
    if "@newsletter" in raw:
        return "jid", raw
    # Código de invitación sin URL
    return "invite", raw


def _parse_newsletter_response(data: dict, fallback_id: str = "") -> dict | None:
    """Extrae {id, subject} de una respuesta de newsletter de Evolution API."""
    if not isinstance(data, dict):
        return None
    jid = (data.get("id") or data.get("jid") or data.get("newsletterId") or
           (data.get("newsletter", {}) or {}).get("id") or fallback_id)
    if not jid:
        return None
    name = (data.get("name") or data.get("subject") or data.get("title") or
            (data.get("newsletter", {}) or {}).get("name") or
            data.get("description") or jid)
    return {"id": jid, "subject": name}


def find_newsletter_by_jid(url: str, api_key: str, instance_name: str, raw_input: str) -> dict | None:
    """
    Resuelve un canal de WhatsApp a partir de:
    - URL pública: https://whatsapp.com/channel/0029Vb3g6PO...
    - Código de invitación: 0029Vb3g6PO...
    - JID directo: 120363XXXXXXXXXX@newsletter

    Devuelve {id, subject} o None.
    """
    hdrs = _headers(api_key)
    kind, value = _parse_channel_input(raw_input)

    if kind == "jid":
        # Ya es un JID — intentar obtener metadatos del canal
        for path, params in (
            (f"/newsletter/find/{instance_name}", {"newsletterId": value}),
            (f"/newsletter/findOne/{instance_name}", {"newsletterId": value}),
            (f"/newsletter/findByJid/{instance_name}", {"jid": value}),
        ):
            try:
                r = requests.get(f"{url}{path}", headers=hdrs, params=params,
                                 timeout=TIMEOUT, verify=VERIFY_SSL)
                if r.status_code in (404, 405, 403):
                    continue
                r.raise_for_status()
                result = _parse_newsletter_response(r.json(), fallback_id=value)
                if result:
                    return result
            except Exception as exc:
                log.debug("find_newsletter jid lookup %s: %s", path, exc)
        # Fallback: JID válido sin nombre
        return {"id": value, "subject": value.replace("@newsletter", "")}

    # kind == "invite" — código de invitación o URL de WhatsApp
    invite_code = value

    # 1. Endpoints específicos de invite code
    for path, params in (
        (f"/newsletter/findByInviteCode/{instance_name}", {"inviteCode": invite_code}),
        (f"/newsletter/findByCode/{instance_name}", {"code": invite_code}),
        (f"/newsletter/find/{instance_name}", {"inviteCode": invite_code}),
        (f"/newsletter/find/{instance_name}", {"link": f"https://whatsapp.com/channel/{invite_code}"}),
        (f"/newsletter/findByLink/{instance_name}", {"link": f"https://whatsapp.com/channel/{invite_code}"}),
    ):
        try:
            r = requests.get(f"{url}{path}", headers=hdrs, params=params,
                             timeout=TIMEOUT, verify=VERIFY_SSL)
            if r.status_code in (404, 405, 403):
                continue
            r.raise_for_status()
            data = r.json()
            # Puede devolver lista o dict
            if isinstance(data, list) and data:
                data = data[0]
            result = _parse_newsletter_response(data)
            if result:
                log.info("find_newsletter resuelto via invite code: %s → %s", invite_code, result["id"])
                return result
        except Exception as exc:
            log.debug("find_newsletter invite %s: %s", path, exc)

    # 2. Intentar follow/preview (Evolution API devuelve el JID al seguir un canal)
    wa_link = f"https://whatsapp.com/channel/{invite_code}"
    for path, body in (
        (f"/newsletter/follow/{instance_name}",   {"code": invite_code}),
        (f"/newsletter/follow/{instance_name}",   {"link": wa_link}),
        (f"/newsletter/preview/{instance_name}",  {"code": invite_code, "link": wa_link}),
        (f"/newsletter/info/{instance_name}",     {"code": invite_code}),
        (f"/newsletter/metadata/{instance_name}", {"type": "invite", "key": invite_code}),
    ):
        try:
            r = requests.post(f"{url}{path}", headers=hdrs, json=body,
                              timeout=TIMEOUT, verify=VERIFY_SSL)
            if r.status_code in (404, 405, 403):
                continue
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                data = data[0]
            result = _parse_newsletter_response(data)
            if result and result.get("id") and "@newsletter" in result["id"]:
                log.info("find_newsletter resuelto via %s: %s → %s", path, invite_code, result["id"])
                return result
        except Exception as exc:
            log.debug("find_newsletter POST %s: %s", path, exc)

    # 3. Último fallback: scrapear la página pública de WhatsApp para obtener al menos el nombre
    try:
        page = requests.get(
            wa_link,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AutoNews/1.0)"},
            timeout=10, allow_redirects=True,
        )
        if page.status_code == 200:
            import html as _html
            text = page.text
            # Open Graph title
            m = _re_wa.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', text)
            if not m:
                m = _re_wa.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', text)
            og_title = _html.unescape(m.group(1).strip()) if m else ""
            # Título de la página
            if not og_title:
                m = _re_wa.search(r'<title>([^<]+)</title>', text)
                og_title = _html.unescape(m.group(1).strip()) if m else ""
            # Buscar JID en el HTML (algunos clientes o SSR lo emiten)
            m_jid = _re_wa.search(r'\b(120363\d+@newsletter)\b', text)
            if m_jid:
                jid_found = m_jid.group(1)
                log.info("find_newsletter: JID encontrado en HTML: %s", jid_found)
                return {"id": jid_found, "subject": og_title or jid_found}
            if og_title:
                log.info("find_newsletter: sin JID pero nombre obtenido desde web: %s", og_title)
                # Devolvemos None como id para que la ruta sepa que no tenemos JID
                return {"id": "", "subject": og_title, "invite_code": invite_code}
    except Exception as exc:
        log.debug("find_newsletter web scrape error: %s", exc)

    return None


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


def _newsletter_jid_variants(jid: str) -> list[str]:
    """
    Evolution API v2.3.x representa los canales de WhatsApp con JID @g.us (ej: 120363...@g.us),
    no @newsletter. Devuelve primero @g.us (que funciona) y luego @newsletter como fallback.
    Los canales siempre tienen base numérica que empieza con 120363.
    """
    base = jid.split("@")[0]
    return [f"{base}@g.us", f"{base}@newsletter"]


def send_to_newsletter(url: str, api_key: str, instance_name: str, newsletter_jid: str, text: str) -> bool:
    """Envía texto a un canal WhatsApp. Prueba @newsletter y @g.us en múltiples endpoints."""
    hdrs = _headers(api_key)
    jid_variants = _newsletter_jid_variants(newsletter_jid)

    jid = jid_variants[0]  # siempre @newsletter

    attempts = [
        (f"/newsletter/sendText/{instance_name}", {"newsletterId": jid, "text": text}),
        (f"/newsletter/send/{instance_name}",     {"newsletterId": jid, "message": {"conversation": text}}),
        (f"/newsletter/send/{instance_name}",     {"newsletterId": jid, "text": text}),
        (f"/message/sendText/{instance_name}",    {"number": jid, "text": text}),
        (f"/message/sendText/{instance_name}",    {"number": jid, "textMessage": {"text": text}}),
    )

    for path, body in attempts:
        try:
            r = requests.post(f"{url}{path}", headers=hdrs, json=body,
                              timeout=TIMEOUT, verify=VERIFY_SSL)
            if r.status_code in (404, 405):
                log.debug("send_to_newsletter %s → 404/405", path)
                continue
            if not r.ok:
                log.warning("send_to_newsletter %s %s → HTTP %d: %s",
                            path, jid[:40], r.status_code, r.text[:300])
                continue
            log.info("send_to_newsletter → %s via %s: OK", jid[:40], path)
            return True
        except Exception as exc:
            log.warning("send_to_newsletter %s: %s", path, exc)

    log.warning("send_to_newsletter FALLÓ para %s — ningún endpoint de canal funcionó",
                newsletter_jid[:40])
    return False


def send_image_to_newsletter(
    url: str, api_key: str, instance_name: str,
    newsletter_jid: str, image_bytes: bytes, mimetype: str, caption: str = "",
) -> bool:
    """Envía imagen a un canal WhatsApp. Prueba @newsletter y @g.us."""
    import base64 as b64lib
    hdrs = _headers(api_key)
    b64 = b64lib.b64encode(image_bytes).decode()
    jid_variants = _newsletter_jid_variants(newsletter_jid)

    for jid in jid_variants:
        for path, body in (
            (f"/newsletter/sendMedia/{instance_name}", {
                "newsletterId": jid, "mediatype": "image",
                "mimetype": mimetype.split(";")[0].strip(), "caption": caption, "media": b64,
            }),
            (f"/message/sendMedia/{instance_name}", {
                "number": jid, "mediatype": "image",
                "mimetype": mimetype.split(";")[0].strip(), "caption": caption, "media": b64,
            }),
        ):
            try:
                r = requests.post(f"{url}{path}", headers=hdrs, json=body,
                                  timeout=TIMEOUT, verify=VERIFY_SSL)
                if r.status_code in (404, 405):
                    log.debug("send_image_to_newsletter %s %s → 404/405", path, jid[:30])
                    continue
                if not r.ok:
                    log.warning("send_image_to_newsletter %s %s → HTTP %d: %s",
                                path, jid[:40], r.status_code, r.text[:300])
                    continue
                log.info("send_image_to_newsletter → %s via %s: OK", jid[:40], path)
                return True
            except Exception as exc:
                log.warning("send_image_to_newsletter %s %s: %s", path, jid[:40], exc)

    log.warning("send_image_to_newsletter FALLÓ para %s — revisá los logs", newsletter_jid[:40])
    return False


def send_image_base64(
    url: str, api_key: str, instance_name: str,
    jid: str, image_bytes: bytes, mimetype: str, caption: str = "",
) -> bool:
    """Envía una imagen como base64 a un número o grupo de WhatsApp."""
    import base64 as b64lib
    try:
        b64 = b64lib.b64encode(image_bytes).decode()
        r = requests.post(
            f"{url}/message/sendMedia/{instance_name}",
            headers=_headers(api_key),
            json={
                "number": jid,
                "mediatype": "image",
                "mimetype": mimetype.split(";")[0].strip(),
                "caption": caption,
                "media": b64,
            },
            timeout=TIMEOUT, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("send_image_base64 to %s error: %s", jid, exc)
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
