from __future__ import annotations

import logging
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
        msg_type = msg["type"]

        if msg_type == "image" and raw_data:
            result = get_media_base64(s.evolution_api_url, s.evolution_api_key, s.instance_name, raw_data)
            if result:
                import mimetypes as _mt
                img_bytes, img_mime = result
                ext = _mt.guess_extension(img_mime) or ".jpg"
                media_data = (img_bytes, f"wa_image{ext}", img_mime)
                log.info("WA: imagen descargada (%d bytes, %s)", len(img_bytes), img_mime)

        elif msg_type == "audio" and raw_data:
            result = get_media_base64(s.evolution_api_url, s.evolution_api_key, s.instance_name, raw_data)
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
                    audio_transcript = "Nota de voz recibida por WhatsApp"

        # Para video/documento: el caption ya viene en msg["text"]
        # Construir texto final
        final_text = audio_transcript or text
        if not final_text and not media_data:
            log.info("WA: mensaje sin contenido procesable (%s)", msg_type)
            return

        _publish_whatsapp_news(db, s, final_text, media_data, msg)
    except Exception as exc:
        log.error("_process_wa_message error: %s", exc)
    finally:
        db.close()


def _publish_whatsapp_news(db, settings, text: str, media_data, msg: dict):
    """Procesa con IA y publica en WordPress el contenido recibido por WA."""
    from app.models import GroqSettings, WordPressSettings, Post
    from app.services.groq_service import process_email_with_groq
    from app.services.wordpress_service import create_post, get_or_create_category, get_or_create_tags, upload_media
    from app.crypto import decrypt_value

    groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
    if not groq_cfg:
        log.warning("WA: no hay configuración de IA activa")
        return

    wp_sites = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).all()
    if not wp_sites:
        log.warning("WA: no hay sitios WordPress activos")
        return

    api_key = decrypt_value(groq_cfg.encrypted_api_key)

    from app.services.wordpress_service import get_categories
    all_cats = []
    for wp in wp_sites:
        try:
            wp_pwd = decrypt_value(wp.encrypted_app_password)
            cats = get_categories(wp.site_url, wp.api_user, wp_pwd)
            all_cats = [c["name"] for c in cats]
            break
        except Exception:
            pass

    subject = text[:100] if text else "Noticia por WhatsApp"
    body = text or "(sin texto)"

    ai_result = process_email_with_groq(
        api_key,
        groq_cfg.model,
        groq_cfg.base_prompt,
        subject,
        body,
        available_categories=all_cats or None,
        provider=groq_cfg.provider,
        api_base_url=groq_cfg.api_base_url,
    )

    img_payload = None
    if media_data:
        img_payload = media_data  # ya es (bytes, filename, mimetype)

    for wp_cfg in wp_sites:
        try:
            wp_pwd = decrypt_value(wp_cfg.encrypted_app_password)

            cat_name = ai_result.get("category", "General")
            try:
                cat_id = get_or_create_category(wp_cfg.site_url, wp_cfg.api_user, wp_pwd, cat_name)
                category_ids = [cat_id] if cat_id else []
            except Exception:
                category_ids = []

            tag_ids = []
            raw_tags = ai_result.get("tags", [])
            if isinstance(raw_tags, list) and raw_tags:
                try:
                    tag_ids = get_or_create_tags(wp_cfg.site_url, wp_cfg.api_user, wp_pwd, raw_tags)
                except Exception:
                    pass

            featured_media_id = None
            if img_payload:
                try:
                    img_bytes, img_name, img_mime = img_payload
                    featured_media_id = upload_media(
                        wp_cfg.site_url, wp_cfg.api_user, wp_pwd,
                        img_bytes, img_name, img_mime,
                    )
                except Exception as exc:
                    log.warning("WA: no se pudo subir imagen: %s", exc)

            wp_post = create_post(
                wp_cfg.site_url, wp_cfg.api_user, wp_pwd,
                ai_result.get("title", subject),
                ai_result.get("content", body),
                wp_cfg.default_status,
                category_ids,
                featured_media_id,
                excerpt=ai_result.get("summary", ""),
                tag_ids=tag_ids,
                keyphrase=ai_result.get("keyphrase", ""),
            )

            db.add(Post(
                processed_email_id=None,
                wordpress_post_id=wp_post.get("id"),
                title=ai_result.get("title", subject),
                content=ai_result.get("content", body),
                category=ai_result.get("category", ""),
                status=wp_cfg.default_status,
                wp_link=wp_post.get("link", ""),
                source_name="WhatsApp",
            ))
            db.commit()
            log.info("WA: publicado en %s — %s", wp_cfg.name, ai_result.get("title", ""))

            # Difundir a grupos si está habilitado
            _broadcast_post(db, settings, ai_result, wp_post.get("link", ""), img_payload)

        except Exception as exc:
            log.error("WA: error publicando en %s: %s", wp_cfg.name, exc)


def _broadcast_post(db, settings, ai_result: dict, wp_url: str, img_payload=None):
    """Envía el artículo publicado a los grupos de WA configurados."""
    if not settings.broadcast_enabled:
        return

    groups = db.query(WhatsAppGroup).filter(WhatsAppGroup.enabled == True).all()
    if not groups:
        return

    from app.services.whatsapp_service import send_text, send_image

    title = ai_result.get("title", "")
    summary = ai_result.get("summary", "")
    template = settings.broadcast_template or "*{title}*\n\n{summary}\n\n{url}"
    text = template.replace("{title}", title).replace("{summary}", summary).replace("{url}", wp_url)

    for g in groups:
        if img_payload and wp_url:
            ok = send_image(
                settings.evolution_api_url, settings.evolution_api_key,
                settings.instance_name, g.jid, wp_url, text,
            )
            if not ok:
                send_text(settings.evolution_api_url, settings.evolution_api_key,
                          settings.instance_name, g.jid, text)
        else:
            send_text(settings.evolution_api_url, settings.evolution_api_key,
                      settings.instance_name, g.jid, text)
