from __future__ import annotations

import html as _html_mod
import logging
import re
import threading

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import WhatsAppGroup, WhatsAppSettings

log = logging.getLogger("whatsapp_route")

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

MAX_GROUPS = 5

_URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]+>')


def _sanitize_text(text: str) -> str:
    """
    Elimina caracteres corruptos del texto antes de enviar a IA o a WhatsApp.
    Resuelve el problema de sitios que devuelven Windows-1252 declarado como UTF-8
    (aparecen como ◆ U+FFFD en el resultado).
    """
    import unicodedata

    # 1. Intentar reparar mojibake (Latin-1 leído como UTF-8)
    try:
        repaired = text.encode('latin-1').decode('utf-8')
        text = repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass  # ya estaba bien o no es Latin-1

    # 2. Eliminar U+FFFD (replacement char) y caracteres de control
    clean = []
    for ch in text:
        if ch in ('\n', '\t'):
            clean.append(ch)
        elif ch == '\r':
            clean.append('\n')
        elif unicodedata.category(ch)[0] == 'C':
            continue  # control char — descartar
        else:
            clean.append(ch)

    result = ''.join(clean)
    # 3. Normalizar a NFC (elimina duplicados de combinación)
    return unicodedata.normalize('NFC', result)

# Patrones de ruido típicos del scraping de portales de noticias
_NOISE_LINE_RE = re.compile(
    r'^\s*('
    r'\+|[-–—]{2,}'                          # separadores
    r'|[\d]{1,2}/[\d]{1,2}/[\d]{2,4}.*'      # fechas
    r'|\d{1,2}:\d{2}\s*(am|pm)?'             # horas
    r'|seguinos(\s+en)?'                      # "Seguinos en"
    r'|compartir|copiar(\s+(enlace|link))?'  # botones sociales
    r'|publicidad|suscri\w*|newsletter'
    r'|comentar|imprimir|relacionad\w*'
    r'|whatsapp|facebook|twitter|instagram|tiktok|youtube'
    r'|leer\s+m[aá]s|ver\s+m[aá]s|click\s+aqu[ií]'
    r'|tags?:|etiquetas?:'
    r')\s*$',
    re.IGNORECASE,
)


def _clean_scrape_noise(text: str) -> str:
    """Elimina ruido típico del scraping: fechas, botones sociales, categorías cortas."""
    lines = text.splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue
        # Saltar líneas cortas que son categorías / etiquetas / noise
        if len(stripped) < 45 and _NOISE_LINE_RE.match(stripped):
            continue
        result.append(line)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(result)).strip()


def _html_to_plain(html_text: str, max_chars: int = 3000) -> str:
    """Convierte HTML de artículo a texto plano con párrafos bien espaciados para WhatsApp."""
    # Convertir subtítulos en negrita WhatsApp antes de eliminar tags
    text = re.sub(r'<h[2-4][^>]*>(.*?)</h[2-4]>', r'\n\n*\1*\n', html_text, flags=re.IGNORECASE | re.DOTALL)
    # Párrafos y listas → doble salto (respira bien en WhatsApp)
    text = re.sub(r'</(p|li)>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    # Eliminar etiquetas restantes
    text = _TAG_RE.sub('', text)
    # Decodificar entidades HTML (&amp; &nbsp; etc.)
    text = _html_mod.unescape(text)
    # Colapsar espacios horizontales y líneas en blanco excesivas
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' \n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    if len(text) <= max_chars:
        return text

    # Cortar en el último punto antes del límite
    cut = text[:max_chars]
    last_dot = cut.rfind('.')
    if last_dot > max_chars // 2:
        return cut[:last_dot + 1]
    return cut.rstrip() + "…"


def _ensure_tables():
    """Crea las tablas de WhatsApp si no existen (fallback por si la migración no corrió)."""
    from sqlalchemy import inspect, text
    from app.database import engine
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    with engine.begin() as conn:
        if "whatsapp_settings" not in tables:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS whatsapp_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evolution_api_url VARCHAR(300) DEFAULT 'http://localhost:8080',
                    evolution_api_key VARCHAR(300) DEFAULT '',
                    instance_name VARCHAR(100) DEFAULT 'botnews',
                    enabled BOOLEAN DEFAULT 0,
                    authorized_numbers TEXT DEFAULT '',
                    broadcast_enabled BOOLEAN DEFAULT 0,
                    broadcast_template TEXT DEFAULT '*{title}*\n\n{summary}\n\n{url}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME
                )
            """))
        if "whatsapp_groups" not in tables:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS whatsapp_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    jid VARCHAR(200) NOT NULL UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))


def _get_settings(db: Session) -> WhatsAppSettings:
    try:
        s = db.query(WhatsAppSettings).first()
    except Exception:
        _ensure_tables()
        db.expire_all()
        s = None
    if not s:
        s = WhatsAppSettings()
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


def _require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return None
    return user


# ── Página principal de configuración ─────────────────────────────────────────

@router.get("/settings/whatsapp", response_class=HTMLResponse)
async def whatsapp_settings(request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    settings = _get_settings(db)
    groups = db.query(WhatsAppGroup).order_by(WhatsAppGroup.id).all()
    return templates.TemplateResponse(
        "settings_whatsapp.html",
        {"request": request, "user": user, "s": settings, "groups": groups, "max_groups": MAX_GROUPS},
    )


# ── Guardar configuración ──────────────────────────────────────────────────────

@router.post("/settings/whatsapp/save")
async def whatsapp_save(
    request: Request,
    db: Session = Depends(get_db),
    evolution_api_url: str = Form(""),
    evolution_api_key: str = Form(""),
    instance_name: str = Form("botnews"),
    enabled: str = Form("off"),
    authorized_numbers: str = Form(""),
    broadcast_enabled: str = Form("off"),
    broadcast_template: str = Form(""),
):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    s = _get_settings(db)
    s.evolution_api_url = evolution_api_url.rstrip("/")
    s.evolution_api_key = evolution_api_key
    s.instance_name = instance_name or "botnews"
    s.enabled = enabled == "on"
    s.authorized_numbers = authorized_numbers.strip()
    s.broadcast_enabled = broadcast_enabled == "on"
    if broadcast_template.strip():
        s.broadcast_template = broadcast_template.strip()
    db.commit()
    return RedirectResponse("/settings/whatsapp?saved=1", status_code=302)


# ── Crear instancia ────────────────────────────────────────────────────────────

@router.post("/settings/whatsapp/create-instance")
async def create_instance(request: Request, db: Session = Depends(get_db)):
    try:
        user = _require_admin(request, db)
        if not user:
            return JSONResponse({"error": "No autorizado"}, status_code=403)
        s = _get_settings(db)
        if not s.evolution_api_url or not s.evolution_api_key:
            return JSONResponse({"error": "Configurá la URL y API key primero"}, status_code=400)
        from app.services.whatsapp_service import create_instance as svc_create
        result = svc_create(s.evolution_api_url, s.evolution_api_key, s.instance_name)
        return JSONResponse({"ok": True, "data": result})
    except Exception as exc:
        log.error("create_instance error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── QR code ────────────────────────────────────────────────────────────────────

@router.get("/settings/whatsapp/qr")
async def get_qr(request: Request, db: Session = Depends(get_db)):
    try:
        user = _require_admin(request, db)
        if not user:
            return JSONResponse({"error": "No autorizado"}, status_code=403)
        s = _get_settings(db)
        from app.services.whatsapp_service import get_qr as svc_qr
        return JSONResponse(svc_qr(s.evolution_api_url, s.evolution_api_key, s.instance_name))
    except Exception as exc:
        log.error("get_qr error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Estado de conexión ─────────────────────────────────────────────────────────

@router.get("/settings/whatsapp/status")
async def connection_status(request: Request, db: Session = Depends(get_db)):
    try:
        user = _require_admin(request, db)
        if not user:
            return JSONResponse({"error": "No autorizado"}, status_code=403)
        s = _get_settings(db)
        from app.services.whatsapp_service import get_status
        return JSONResponse(get_status(s.evolution_api_url, s.evolution_api_key, s.instance_name))
    except Exception as exc:
        log.error("connection_status error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Configurar webhook en Evolution API ────────────────────────────────────────

@router.post("/settings/whatsapp/set-webhook")
async def configure_webhook(
    request: Request,
    db: Session = Depends(get_db),
    webhook_base_url: str = Form(""),
):
    try:
        user = _require_admin(request, db)
        if not user:
            return JSONResponse({"error": "No autorizado"}, status_code=403)
        s = _get_settings(db)
        webhook_url = webhook_base_url.rstrip("/") + "/webhook/whatsapp"
        from app.services.whatsapp_service import set_webhook
        ok = set_webhook(s.evolution_api_url, s.evolution_api_key, s.instance_name, webhook_url)
        if ok:
            return JSONResponse({"ok": True, "webhook_url": webhook_url})
        return JSONResponse({"error": "No se pudo configurar el webhook"}, status_code=500)
    except Exception as exc:
        log.error("configure_webhook error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Grupos ─────────────────────────────────────────────────────────────────────

@router.get("/settings/whatsapp/fetch-groups")
async def fetch_groups(request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    s = _get_settings(db)
    from app.services.whatsapp_service import fetch_groups as svc_groups
    groups = svc_groups(s.evolution_api_url, s.evolution_api_key, s.instance_name)
    return JSONResponse({"groups": groups})


@router.post("/settings/whatsapp/groups/add")
async def add_group(
    request: Request,
    db: Session = Depends(get_db),
    jid: str = Form(""),
    name: str = Form(""),
):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    count = db.query(WhatsAppGroup).count()
    if count >= MAX_GROUPS:
        return JSONResponse({"error": f"Máximo {MAX_GROUPS} grupos permitidos"}, status_code=400)

    if not jid.strip():
        return JSONResponse({"error": "JID requerido"}, status_code=400)

    existing = db.query(WhatsAppGroup).filter(WhatsAppGroup.jid == jid.strip()).first()
    if existing:
        return JSONResponse({"error": "Grupo ya agregado"}, status_code=400)

    g = WhatsAppGroup(jid=jid.strip(), name=name.strip() or jid.strip())
    db.add(g)
    db.commit()
    db.refresh(g)
    return JSONResponse({"ok": True, "id": g.id, "jid": g.jid, "name": g.name})


@router.post("/settings/whatsapp/groups/{group_id}/toggle")
async def toggle_group(group_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    g = db.query(WhatsAppGroup).filter(WhatsAppGroup.id == group_id).first()
    if not g:
        return JSONResponse({"error": "No encontrado"}, status_code=404)
    g.enabled = not g.enabled
    db.commit()
    return JSONResponse({"ok": True, "enabled": g.enabled})


@router.post("/settings/whatsapp/groups/{group_id}/delete")
async def delete_group(group_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    g = db.query(WhatsAppGroup).filter(WhatsAppGroup.id == group_id).first()
    if g:
        db.delete(g)
        db.commit()
    return JSONResponse({"ok": True})


# ── Mensaje de prueba ──────────────────────────────────────────────────────────

@router.post("/settings/whatsapp/test-broadcast")
async def test_broadcast(request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    s = _get_settings(db)
    groups = db.query(WhatsAppGroup).filter(WhatsAppGroup.enabled == True).all()
    if not groups:
        return JSONResponse({"error": "No hay grupos activos configurados"}, status_code=400)

    from app.services.whatsapp_service import send_text
    sent, failed = 0, 0
    for g in groups:
        ok = send_text(
            s.evolution_api_url, s.evolution_api_key, s.instance_name,
            g.jid, "✅ *AutoNews* — Prueba de conexión correcta.",
        )
        if ok:
            sent += 1
        else:
            failed += 1

    return JSONResponse({"ok": True, "sent": sent, "failed": failed})


# ── Webhook público (Evolution API → nosotros) ─────────────────────────────────

@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False})

    event = payload.get("event", "")
    if event not in ("messages.upsert", "MESSAGES_UPSERT"):
        return JSONResponse({"ok": True})

    # Procesar en hilo separado para no bloquear FastAPI
    thread = threading.Thread(target=_process_wa_message, args=(payload,), daemon=True)
    thread.start()
    return JSONResponse({"ok": True})


def _process_wa_message(payload: dict):
    """Procesa un mensaje de WhatsApp entrante y publica en WordPress si corresponde."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        from app.services.whatsapp_service import parse_incoming, get_media_base64
        msg = parse_incoming(payload)
        if not msg:
            return

        s = db.query(WhatsAppSettings).first()
        if not s or not s.enabled:
            return

        # Verificar número autorizado (soporta CSV)
        authorized = [n.strip() for n in (s.authorized_numbers or "").split(",") if n.strip()]
        if authorized and msg["from"] not in authorized:
            log.info("WA: mensaje de número no autorizado %s", msg["from"])
            return

        # Ignorar mensajes de grupos (solo procesar DMs)
        if msg["is_group"]:
            log.info("WA: mensaje de grupo ignorado (solo se procesan DMs)")
            return

        text = msg.get("text", "").strip()
        media_data = None   # (bytes, filename, mimetype) para imagen adjunta
        audio_transcript = ""

        raw_data = msg.get("_raw_data", {})
        media_dict = msg.get("media") or {}
        msg_type = msg["type"]

        import mimetypes as _mt

        if msg_type == "image":
            # Intento 1: Evolution API base64
            result = get_media_base64(s.evolution_api_url, s.evolution_api_key, s.instance_name, raw_data) if raw_data else None
            # Intento 2: URL directa del campo imageMessage
            if not result and media_dict:
                from app.services.whatsapp_service import download_media
                dl = download_media(s.evolution_api_url, s.evolution_api_key, s.instance_name, media_dict)
                if dl:
                    raw_b, fname, mime = dl
                    result = (raw_b, mime)
            if result:
                img_bytes, img_mime = result
                ext = _mt.guess_extension(img_mime) or ".jpg"
                media_data = (img_bytes, f"wa_image{ext}", img_mime)
                log.info("WA: imagen descargada (%d bytes, %s)", len(img_bytes), img_mime)

                # OCR/visión: extraer texto de la imagen cuando no hay caption o es muy corto
                if len(text) < 30:
                    from app.models import GroqSettings
                    from app.crypto import decrypt_value
                    groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
                    if groq_cfg:
                        from app.services.groq_service import extract_image_text
                        groq_key = decrypt_value(groq_cfg.encrypted_api_key)
                        ocr = extract_image_text(
                            groq_key, img_bytes, img_mime,
                            provider=groq_cfg.provider or "groq",
                            api_base_url=groq_cfg.api_base_url,
                        )
                        if ocr:
                            text = (text + "\n\n" + ocr).strip() if text else ocr
                            log.info("WA: OCR extraído (%d chars)", len(ocr))
            else:
                log.warning("WA: no se pudo descargar la imagen — se continúa sin foto")
            if not text:
                text = "Foto compartida vía WhatsApp"

        elif msg_type == "audio":
            result = get_media_base64(s.evolution_api_url, s.evolution_api_key, s.instance_name, raw_data) if raw_data else None
            if result:
                audio_bytes, audio_mime = result
                log.info("WA: audio descargado (%d bytes) — transcribiendo…", len(audio_bytes))
                from app.models import GroqSettings
                from app.crypto import decrypt_value
                groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
                if groq_cfg and (groq_cfg.provider or "groq") == "groq":
                    from app.services.groq_service import transcribe_audio
                    groq_key = decrypt_value(groq_cfg.encrypted_api_key)
                    audio_transcript = transcribe_audio(groq_key, audio_bytes, audio_mime)
                    log.info("WA: transcripción (%d chars): %s…", len(audio_transcript), audio_transcript[:80])
            if not audio_transcript:
                audio_transcript = text or "Nota de voz recibida por WhatsApp"

        # Video/documento: el caption ya viene en msg["text"]
        final_text = audio_transcript or text
        if not final_text:
            log.info("WA: mensaje sin contenido procesable (%s)", msg_type)
            return

        # Detectar si el mensaje contiene una URL para scrapear
        url_match = _URL_RE.search(final_text)
        source_url = url_match.group().rstrip(".,;)>") if url_match else None

        # Mensajes de texto sin URL muy cortos no tienen suficiente contenido para una nota
        if not source_url and msg_type == "text" and len(final_text) < 80:
            log.info("WA: mensaje de texto muy corto sin URL (%d chars) — ignorado", len(final_text))
            return

        _publish_whatsapp_news(db, s, final_text, media_data, source_url, sender_jid=msg["jid"])
    except Exception as exc:
        log.error("_process_wa_message error: %s", exc)
    finally:
        db.close()


def _publish_whatsapp_news(db, settings, text: str, media_data, source_url: str | None, sender_jid: str | None = None):
    """
    Procesa con IA el contenido recibido por WA y difunde a grupos.
    NO publica en WordPress — eso es exclusivo de RSS y Email.
    Si source_url está presente, scrapea el artículo de esa URL.
    """
    from app.models import GroqSettings
    from app.crypto import decrypt_value

    groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
    if not groq_cfg:
        log.warning("WA: no hay configuración de IA activa")
        return

    api_key = decrypt_value(groq_cfg.encrypted_api_key)

    article_body = text
    scraped_image_url = None

    # Si el mensaje tiene una URL: scrapear el artículo completo
    if source_url:
        log.info("WA: URL detectada — scrapeando %s", source_url)
        try:
            from app.services.rss_service import scrape_full_article
            scraped_text, scraped_image_url = scrape_full_article(source_url)
            if scraped_text and len(scraped_text) > 200:
                # Limpiar encoding corrupto, luego ruido del scraping
                article_body = _clean_scrape_noise(_sanitize_text(scraped_text))
                log.info("WA: artículo scrapeado y limpio (%d chars)", len(article_body))
            else:
                log.warning("WA: scraping insuficiente (%d chars)", len(scraped_text or ""))
                article_body = ""
        except Exception as exc:
            log.warning("WA: no se pudo scrapear %s: %s", source_url, exc)
            article_body = ""

        # Si el scraping no dio contenido suficiente, notificar y abortar
        if len(article_body) < 300:
            log.warning("WA: contenido insuficiente tras scraping — abortando")
            if sender_jid:
                from app.services.whatsapp_service import send_text
                send_text(
                    settings.evolution_api_url, settings.evolution_api_key,
                    settings.instance_name, sender_jid,
                    "No pude leer ese artículo (el sitio bloquea el scraping o usa JavaScript). "
                    "Intentá copiar y pegar el texto directamente.",
                )
            return

    # Procesar con IA
    # Para URLs: subject vacío → la IA genera el título únicamente desde el contenido
    if source_url:
        subject = ""
    else:
        subject = article_body[:100] if article_body else "Noticia por WhatsApp"

    if source_url and len(article_body) > 300:
        from app.services.groq_service import process_rss_with_groq
        ai_result = process_rss_with_groq(
            api_key, groq_cfg.model, groq_cfg.base_prompt,
            subject, article_body,
            provider=groq_cfg.provider,
            api_base_url=groq_cfg.api_base_url,
        )
    else:
        from app.services.groq_service import process_email_with_groq
        ai_result = process_email_with_groq(
            api_key, groq_cfg.model, groq_cfg.base_prompt,
            subject, article_body,
            provider=groq_cfg.provider,
            api_base_url=groq_cfg.api_base_url,
        )

    log.info("WA: IA generó — %s", ai_result.get("title", "")[:80])

    # Difundir a grupos (sin link externo — el cierre usa la URL del sitio WordPress)
    _broadcast_whatsapp(db, settings, ai_result, media_data, scraped_image_url)


def _broadcast_whatsapp(
    db, settings, ai_result: dict,
    img_payload=None,
    fallback_image_url: str | None = None,
):
    """Envía la noticia completa a los grupos de WA. Cierra con el sitio WordPress."""
    if not settings.broadcast_enabled:
        log.info("WA broadcast: difusión deshabilitada")
        return

    groups = db.query(WhatsAppGroup).filter(WhatsAppGroup.enabled == True).all()
    if not groups:
        log.info("WA broadcast: no hay grupos activos configurados")
        return

    from app.services.whatsapp_service import send_text, send_image_base64, send_image

    # Título limpio: sin saltos de línea (rompen el bold en WhatsApp)
    title = re.sub(r'\s+', ' ', ai_result.get("title", "")).strip()
    content_html = ai_result.get("content", "")
    summary = ai_result.get("summary", "")

    # Cuerpo: artículo completo en texto plano
    body = _html_to_plain(content_html, max_chars=3000) if content_html else summary

    # URL del sitio WordPress como cierre (sin link externo a la fuente)
    site_url = ""
    try:
        from app.models import WordPressSettings
        wp_cfg = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).first()
        if wp_cfg:
            site_url = wp_cfg.site_url.rstrip("/")
    except Exception:
        pass

    # Sanitizar encoding antes de enviar (elimina ◆ U+FFFD y chars corruptos)
    title = _sanitize_text(title)
    body = _sanitize_text(body)

    domain = site_url.replace("https://", "").replace("http://", "").rstrip("/") if site_url else ""
    msg_text = f"*{title}*\n\n{body}"
    if domain:
        msg_text += f"\n\nTodas las noticias en {domain}"

    log.info("WA broadcast: enviando a %d grupo(s) — %s", len(groups), title[:60])
    for g in groups:
        sent = False
        # 1. Imagen adjunta recibida (bytes)
        if img_payload:
            img_bytes, _img_name, img_mime = img_payload
            sent = send_image_base64(
                settings.evolution_api_url, settings.evolution_api_key,
                settings.instance_name, g.jid, img_bytes, img_mime, msg_text,
            )
        # 2. Imagen scrapeada del artículo — descargar y enviar como base64
        #    (evita bloqueos de hotlinking del sitio fuente)
        if not sent and fallback_image_url:
            try:
                import httpx as _httpx
                ir = _httpx.get(
                    fallback_image_url, timeout=15, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                )
                ir.raise_for_status()
                img_mime = ir.headers.get("content-type", "image/jpeg").split(";")[0]
                sent = send_image_base64(
                    settings.evolution_api_url, settings.evolution_api_key,
                    settings.instance_name, g.jid, ir.content, img_mime, msg_text,
                )
            except Exception as _exc:
                log.warning("WA: no se pudo descargar og:image %s: %s", fallback_image_url, _exc)
            # Último recurso: URL directa
            if not sent:
                sent = send_image(
                    settings.evolution_api_url, settings.evolution_api_key,
                    settings.instance_name, g.jid, fallback_image_url, msg_text,
                )
        # 3. Solo texto
        if not sent:
            sent = send_text(
                settings.evolution_api_url, settings.evolution_api_key,
                settings.instance_name, g.jid, msg_text,
            )
        log.info("WA broadcast → %s (%s): %s", g.name, g.jid, "ok" if sent else "ERROR")
