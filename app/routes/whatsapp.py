from __future__ import annotations

import html as _html_mod
import logging
import re
import threading
import time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import WhatsAppChannel, WhatsAppGroup, WhatsAppSettings, WordPressSettings

log = logging.getLogger("whatsapp_route")

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

MAX_GROUPS = 5
MAX_CHANNELS = 5

_URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]+>')
_WA_BOLD_LINE_RE = re.compile(r'^\*([^*\n]+)\*\s*$')
_WA_BOLD_INLINE_RE = re.compile(r'\*([^*\n]{1,300})\*')


def _preprocess_wa_text(text: str) -> tuple[str, str]:
    """Limpia formato negrita WhatsApp (*texto*) y detecta posible título.
    - Líneas completamente entre ** → se convierten en texto limpio y la primera con
      más de 15 chars se toma como pista de título.
    - Negritas inline (*palabra*) → se eliminan los asteriscos.
    Devuelve (texto_limpio, título_hint)."""
    title_hint = ""
    clean_lines = []
    for line in text.splitlines():
        m = _WA_BOLD_LINE_RE.match(line.strip())
        if m:
            inner = m.group(1).strip()
            if not title_hint and len(inner) > 15:
                title_hint = inner
            clean_lines.append(inner)
        else:
            clean_lines.append(_WA_BOLD_INLINE_RE.sub(r'\1', line))
    return '\n'.join(clean_lines), title_hint


def _sanitize_text(text: str) -> str:
    import unicodedata
    try:
        repaired = text.encode('latin-1').decode('utf-8')
        text = repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    clean = []
    for ch in text:
        if ch in ('\n', '\t'):
            clean.append(ch)
        elif ch == '\r':
            clean.append('\n')
        elif unicodedata.category(ch)[0] == 'C':
            continue
        else:
            clean.append(ch)
    result = ''.join(clean)
    return unicodedata.normalize('NFC', result)


_NOISE_LINE_RE = re.compile(
    r'^\s*('
    r'\+|[-–—]{2,}'
    r'|[\d]{1,2}/[\d]{1,2}/[\d]{2,4}.*'
    r'|\d{1,2}:\d{2}\s*(am|pm)?'
    r'|seguinos(\s+en)?'
    r'|compartir|copiar(\s+(enlace|link))?'
    r'|publicidad|suscri\w*|newsletter'
    r'|comentar|imprimir|relacionad\w*'
    r'|whatsapp|facebook|twitter|instagram|tiktok|youtube'
    r'|leer\s+m[aá]s|ver\s+m[aá]s|click\s+aqu[ií]'
    r'|tags?:|etiquetas?:'
    r')\s*$',
    re.IGNORECASE,
)


def _clean_scrape_noise(text: str) -> str:
    lines = text.splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue
        if len(stripped) < 45 and _NOISE_LINE_RE.match(stripped):
            continue
        result.append(line)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(result)).strip()


def _html_to_plain(html_text: str, max_chars: int = 3000) -> str:
    text = re.sub(r'<h[2-4][^>]*>(.*?)</h[2-4]>', r'\n\n*\1*\n', html_text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'</(p|li)>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = _TAG_RE.sub('', text)
    text = _html_mod.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' \n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_dot = cut.rfind('.')
    if last_dot > max_chars // 2:
        return cut[:last_dot + 1]
    return cut.rstrip() + "…"


def _require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return None
    return user


def _get_account(db: Session, wa_id: int) -> WhatsAppSettings | None:
    return db.query(WhatsAppSettings).filter(WhatsAppSettings.id == wa_id).first()


# ── Página principal ───────────────────────────────────────────────────────────

@router.get("/settings/whatsapp", response_class=HTMLResponse)
async def whatsapp_settings(request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    accounts = db.query(WhatsAppSettings).order_by(WhatsAppSettings.id).all()
    if not accounts:
        default = WhatsAppSettings(name="Principal")
        db.add(default)
        db.commit()
        db.refresh(default)
        accounts = [default]

    for acc in accounts:
        acc._groups = db.query(WhatsAppGroup).filter(
            WhatsAppGroup.whatsapp_settings_id == acc.id
        ).order_by(WhatsAppGroup.id).all()
        acc._channels = db.query(WhatsAppChannel).filter(
            WhatsAppChannel.whatsapp_settings_id == acc.id
        ).order_by(WhatsAppChannel.id).all()

    wp_sites = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).order_by(WordPressSettings.id).all()
    from app.models import InstagramSettings
    ig_accounts = db.query(InstagramSettings).order_by(InstagramSettings.id).all()
    return templates.TemplateResponse(
        "settings_whatsapp.html",
        {
            "request": request, "user": user,
            "accounts": accounts,
            "wp_sites": wp_sites,
            "ig_accounts": ig_accounts,
            "max_groups": MAX_GROUPS,
            "max_channels": MAX_CHANNELS,
        },
    )


# ── CRUD de cuentas ────────────────────────────────────────────────────────────

@router.post("/settings/whatsapp/add")
async def add_whatsapp_account(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form("Principal"),
    evolution_api_url: str = Form(""),
    evolution_api_key: str = Form(""),
    instance_name: str = Form("botnews"),
    enabled: str = Form("off"),
    authorized_numbers: str = Form(""),
    broadcast_enabled: str = Form("off"),
    broadcast_template: str = Form(""),
    wordpress_settings_id: str = Form(""),
    instagram_settings_id: str = Form(""),
    publish_mode: str = Form("both"),
    rewrite_mode: str = Form("rewrite"),
):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    acc = WhatsAppSettings(
        name=name.strip() or "Nueva cuenta",
        evolution_api_url=evolution_api_url.rstrip("/"),
        evolution_api_key=evolution_api_key,
        instance_name=instance_name.strip() or "botnews",
        enabled=enabled == "on",
        authorized_numbers=authorized_numbers.strip(),
        broadcast_enabled=broadcast_enabled == "on",
        broadcast_template=broadcast_template.strip() or "*{title}*\n\n{summary}\n\n{url}",
        wordpress_settings_id=int(wordpress_settings_id) if wordpress_settings_id.strip().isdigit() else None,
        instagram_settings_id=int(instagram_settings_id) if instagram_settings_id.strip().isdigit() else None,
        publish_mode=publish_mode if publish_mode in ("both", "wordpress_only", "whatsapp_only") else "both",
        rewrite_mode=rewrite_mode if rewrite_mode in ("rewrite", "title_only") else "rewrite",
    )
    db.add(acc)
    db.commit()
    return RedirectResponse("/settings/whatsapp?saved=1", status_code=302)


@router.post("/settings/whatsapp/{wa_id}/edit")
async def edit_whatsapp_account(
    wa_id: int,
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form("Principal"),
    evolution_api_url: str = Form(""),
    evolution_api_key: str = Form(""),
    instance_name: str = Form("botnews"),
    enabled: str = Form("off"),
    authorized_numbers: str = Form(""),
    broadcast_enabled: str = Form("off"),
    broadcast_template: str = Form(""),
    wordpress_settings_id: str = Form(""),
    instagram_settings_id: str = Form(""),
    publish_mode: str = Form("both"),
    rewrite_mode: str = Form("rewrite"),
):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    acc = _get_account(db, wa_id)
    if not acc:
        return RedirectResponse("/settings/whatsapp", status_code=302)

    acc.name = name.strip() or "Principal"
    acc.evolution_api_url = evolution_api_url.rstrip("/")
    acc.evolution_api_key = evolution_api_key
    acc.instance_name = instance_name.strip() or "botnews"
    acc.enabled = enabled == "on"
    acc.authorized_numbers = authorized_numbers.strip()
    acc.broadcast_enabled = broadcast_enabled == "on"
    if broadcast_template.strip():
        acc.broadcast_template = broadcast_template.strip()
    acc.wordpress_settings_id = int(wordpress_settings_id) if wordpress_settings_id.strip().isdigit() else None
    acc.instagram_settings_id = int(instagram_settings_id) if instagram_settings_id.strip().isdigit() else None
    acc.publish_mode = publish_mode if publish_mode in ("both", "wordpress_only", "whatsapp_only") else "both"
    acc.rewrite_mode = rewrite_mode if rewrite_mode in ("rewrite", "title_only") else "rewrite"
    db.commit()
    return RedirectResponse("/settings/whatsapp?saved=1", status_code=302)


@router.post("/settings/whatsapp/{wa_id}/delete")
async def delete_whatsapp_account(wa_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    acc = _get_account(db, wa_id)
    if acc:
        db.delete(acc)
        db.commit()
    return RedirectResponse("/settings/whatsapp", status_code=302)


@router.post("/settings/whatsapp/{wa_id}/toggle")
async def toggle_whatsapp_account(wa_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    acc = _get_account(db, wa_id)
    if not acc:
        return JSONResponse({"error": "No encontrado"}, status_code=404)
    acc.enabled = not acc.enabled
    db.commit()
    return JSONResponse({"ok": True, "enabled": acc.enabled})


# ── Crear instancia ────────────────────────────────────────────────────────────

@router.post("/settings/whatsapp/{wa_id}/create-instance")
async def create_instance(wa_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        user = _require_admin(request, db)
        if not user:
            return JSONResponse({"error": "No autorizado"}, status_code=403)
        acc = _get_account(db, wa_id)
        if not acc:
            return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)
        if not acc.evolution_api_url or not acc.evolution_api_key:
            return JSONResponse({"error": "Configurá la URL y API key primero"}, status_code=400)
        from app.services.whatsapp_service import create_instance as svc_create
        result = svc_create(acc.evolution_api_url, acc.evolution_api_key, acc.instance_name)
        if result.get("already_exists"):
            return JSONResponse({"ok": True, "message": "La instancia ya existe — escaneá el QR si aún no está conectada."})
        return JSONResponse({"ok": True, "message": "Instancia creada. Ahora escaneá el QR.", "data": result})
    except Exception as exc:
        log.error("create_instance error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── QR code ────────────────────────────────────────────────────────────────────

@router.get("/settings/whatsapp/{wa_id}/qr")
async def get_qr(wa_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        user = _require_admin(request, db)
        if not user:
            return JSONResponse({"error": "No autorizado"}, status_code=403)
        acc = _get_account(db, wa_id)
        if not acc:
            return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)
        from app.services.whatsapp_service import get_qr as svc_qr
        return JSONResponse(svc_qr(acc.evolution_api_url, acc.evolution_api_key, acc.instance_name))
    except Exception as exc:
        log.error("get_qr error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Estado de conexión ─────────────────────────────────────────────────────────

@router.get("/settings/whatsapp/{wa_id}/status")
async def connection_status(wa_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        user = _require_admin(request, db)
        if not user:
            return JSONResponse({"error": "No autorizado"}, status_code=403)
        acc = _get_account(db, wa_id)
        if not acc:
            return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)
        from app.services.whatsapp_service import get_status
        return JSONResponse(get_status(acc.evolution_api_url, acc.evolution_api_key, acc.instance_name))
    except Exception as exc:
        log.error("connection_status error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Webhook ────────────────────────────────────────────────────────────────────

@router.post("/settings/whatsapp/{wa_id}/set-webhook")
async def configure_webhook(
    wa_id: int,
    request: Request,
    db: Session = Depends(get_db),
    webhook_base_url: str = Form(""),
):
    try:
        user = _require_admin(request, db)
        if not user:
            return JSONResponse({"error": "No autorizado"}, status_code=403)
        acc = _get_account(db, wa_id)
        if not acc:
            return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)
        webhook_url = webhook_base_url.rstrip("/") + "/webhook/whatsapp"
        from app.services.whatsapp_service import set_webhook
        ok, err = set_webhook(acc.evolution_api_url, acc.evolution_api_key, acc.instance_name, webhook_url)
        if ok:
            return JSONResponse({"ok": True, "webhook_url": webhook_url})
        return JSONResponse({"error": f"No se pudo configurar el webhook — {err}"}, status_code=500)
    except Exception as exc:
        log.error("configure_webhook error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Grupos ─────────────────────────────────────────────────────────────────────

@router.get("/settings/whatsapp/{wa_id}/fetch-groups")
async def fetch_groups(wa_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    acc = _get_account(db, wa_id)
    if not acc:
        return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)
    from app.services.whatsapp_service import fetch_groups as svc_groups
    groups = svc_groups(acc.evolution_api_url, acc.evolution_api_key, acc.instance_name)
    return JSONResponse({"groups": groups})


@router.get("/settings/whatsapp/{wa_id}/fetch-channels")
async def fetch_channels_route(wa_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    acc = _get_account(db, wa_id)
    if not acc:
        return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)
    from app.services.whatsapp_service import fetch_newsletters
    channels, status = fetch_newsletters(acc.evolution_api_url, acc.evolution_api_key, acc.instance_name)
    return JSONResponse({"channels": channels, "status": status})


@router.get("/settings/whatsapp/{wa_id}/find-channel")
async def find_channel_by_jid(wa_id: int, request: Request, jid: str = "", db: Session = Depends(get_db)):
    """Resuelve un canal por URL de WhatsApp, código de invitación o JID directo."""
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)
    acc = _get_account(db, wa_id)
    if not acc:
        return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)
    raw = jid.strip()
    if not raw:
        return JSONResponse({"error": "Ingresá una URL, código o JID"}, status_code=400)
    from app.services.whatsapp_service import find_newsletter_by_jid, _parse_channel_input
    kind, value = _parse_channel_input(raw)
    result = find_newsletter_by_jid(acc.evolution_api_url, acc.evolution_api_key, acc.instance_name, raw)
    if result and result.get("id"):
        return JSONResponse({"ok": True, "channel": result})
    # Obtuvimos el nombre desde la web pero no el JID
    if result and result.get("subject") and not result.get("id"):
        return JSONResponse({
            "ok": False,
            "partial": True,
            "channel_name": result["subject"],
            "invite_code": result.get("invite_code", value),
            "error": (
                f"Se encontró el canal «{result['subject']}» pero tu versión de Evolution API "
                "no permite obtener el JID interno automáticamente. "
                "Mirá las instrucciones debajo para obtenerlo."
            ),
        })
    if kind == "invite":
        return JSONResponse({
            "ok": False,
            "invite_code": value,
            "error": (
                "No se pudo resolver el canal automáticamente. "
                "Mirá las instrucciones debajo para obtener el JID."
            ),
        })
    return JSONResponse({"ok": False, "error": "Canal no encontrado"})


@router.post("/settings/whatsapp/{wa_id}/groups/add")
async def add_group(
    wa_id: int,
    request: Request,
    db: Session = Depends(get_db),
    jid: str = Form(""),
    name: str = Form(""),
):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    acc = _get_account(db, wa_id)
    if not acc:
        return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)

    count = db.query(WhatsAppGroup).filter(WhatsAppGroup.whatsapp_settings_id == wa_id).count()
    if count >= MAX_GROUPS:
        return JSONResponse({"error": f"Máximo {MAX_GROUPS} grupos por cuenta"}, status_code=400)

    if not jid.strip():
        return JSONResponse({"error": "JID requerido"}, status_code=400)

    existing = db.query(WhatsAppGroup).filter(WhatsAppGroup.jid == jid.strip()).first()
    if existing:
        return JSONResponse({"error": "Grupo ya agregado"}, status_code=400)

    g = WhatsAppGroup(jid=jid.strip(), name=name.strip() or jid.strip(), whatsapp_settings_id=wa_id)
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


class AssignWPRequest(BaseModel):
    wordpress_settings_id: int | None = None


@router.post("/settings/whatsapp/groups/{group_id}/assign-wp")
async def assign_wp_to_group(
    group_id: int,
    payload: AssignWPRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    g = db.query(WhatsAppGroup).filter(WhatsAppGroup.id == group_id).first()
    if not g:
        return JSONResponse({"error": "Grupo no encontrado"}, status_code=404)

    g.wordpress_settings_id = payload.wordpress_settings_id
    db.commit()
    return JSONResponse({"ok": True, "wordpress_settings_id": g.wordpress_settings_id})


# ── Canales ───────────────────────────────────────────────────────────────────

@router.post("/settings/whatsapp/{wa_id}/channels/add")
async def add_channel(
    wa_id: int,
    request: Request,
    db: Session = Depends(get_db),
    jid: str = Form(""),
    name: str = Form(""),
):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    acc = _get_account(db, wa_id)
    if not acc:
        return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)

    count = db.query(WhatsAppChannel).filter(WhatsAppChannel.whatsapp_settings_id == wa_id).count()
    if count >= MAX_CHANNELS:
        return JSONResponse({"error": f"Máximo {MAX_CHANNELS} canales por cuenta"}, status_code=400)

    if not jid.strip():
        return JSONResponse({"error": "JID requerido"}, status_code=400)

    existing = db.query(WhatsAppChannel).filter(WhatsAppChannel.jid == jid.strip()).first()
    if existing:
        return JSONResponse({"error": "Canal ya agregado"}, status_code=400)

    ch = WhatsAppChannel(jid=jid.strip(), name=name.strip() or jid.strip(), whatsapp_settings_id=wa_id)
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return JSONResponse({"ok": True, "id": ch.id, "jid": ch.jid, "name": ch.name})


@router.post("/settings/whatsapp/channels/{channel_id}/toggle")
async def toggle_channel(channel_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    ch = db.query(WhatsAppChannel).filter(WhatsAppChannel.id == channel_id).first()
    if not ch:
        return JSONResponse({"error": "No encontrado"}, status_code=404)
    ch.enabled = not ch.enabled
    db.commit()
    return JSONResponse({"ok": True, "enabled": ch.enabled})


@router.post("/settings/whatsapp/channels/{channel_id}/delete")
async def delete_channel(channel_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    ch = db.query(WhatsAppChannel).filter(WhatsAppChannel.id == channel_id).first()
    if ch:
        db.delete(ch)
        db.commit()
    return JSONResponse({"ok": True})


# ── Prueba de difusión ─────────────────────────────────────────────────────────

@router.post("/settings/whatsapp/{wa_id}/test-broadcast")
async def test_broadcast(wa_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=403)

    acc = _get_account(db, wa_id)
    if not acc:
        return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)

    groups = db.query(WhatsAppGroup).filter(
        WhatsAppGroup.enabled == True,
        WhatsAppGroup.whatsapp_settings_id == wa_id,
    ).all()
    if not groups:
        return JSONResponse({"error": "No hay grupos activos para esta cuenta"}, status_code=400)

    from app.services.whatsapp_service import send_text
    sent, failed = 0, 0
    for g in groups:
        ok = send_text(
            acc.evolution_api_url, acc.evolution_api_key, acc.instance_name,
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

    try:
        import json as _json
        raw = _json.dumps(payload)
        import re as _re
        newsletter_jids = list(set(_re.findall(r'[\w\-]+@newsletter', raw)))
        if newsletter_jids:
            log.info("WA CANAL detectado — evento=%s JID(s): %s", event, newsletter_jids)
        elif event not in ("messages.upsert", "MESSAGES_UPSERT", "CONNECTION_UPDATE"):
            log.info("WA webhook evento desconocido: %s — payload: %.400s", event, raw)
    except Exception:
        pass

    if event not in ("messages.upsert", "MESSAGES_UPSERT"):
        return JSONResponse({"ok": True})

    thread = threading.Thread(target=_process_wa_message, args=(payload,), daemon=True)
    thread.start()
    return JSONResponse({"ok": True})


def _log_db(db, level: str, message: str):
    try:
        from app.models import Log
        db.add(Log(level=level, message=message, source="whatsapp"))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


# ── Buffer de mensajes multi-parte ────────────────────────────────────────────

_BUFFER_SECS = 15        # Espera inicial para mensajes de texto multi-parte
_BUFFER_QUICK_SECS = 3  # Espera tras texto+imagen para capturar imágenes adicionales
_MAX_IMAGES = 4          # 1 portada + hasta 3 inline

# key: "{instance_name}:{sender_number}"
_wa_buffers: dict[str, dict] = {}
_wa_buf_lock = threading.Lock()


def _flush_wa_buffer(buf_key: str):
    with _wa_buf_lock:
        buf = _wa_buffers.pop(buf_key, None)
    if not buf:
        return

    text = buf["text"]
    media_list = buf.get("media_list", [])
    source_url = buf["source_url"]
    jid = buf["jid"]
    instance_name = buf.get("instance_name", "")
    wa_title_hint = buf.get("wa_title_hint", "")

    if not text and not media_list:
        return

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if instance_name:
            s = db.query(WhatsAppSettings).filter(WhatsAppSettings.instance_name == instance_name).first()
        else:
            s = db.query(WhatsAppSettings).first()
        if s:
            _publish_whatsapp_news(db, s, text or "Foto compartida vía WhatsApp",
                                   media_list, source_url, sender_jid=jid,
                                   wa_title_hint=wa_title_hint)
    except Exception as exc:
        log.error("_flush_wa_buffer error: %s", exc)
    finally:
        db.close()


def _buffer_wa_content(buf_key: str, jid: str, text: str, media_payload, source_url: str | None, instance_name: str, wa_title_hint: str = ""):
    """media_payload = (bytes, name, mime) de una sola imagen, o None."""
    with _wa_buf_lock:
        existing = _wa_buffers.get(buf_key)

        if existing:
            existing["timer"].cancel()
            combined_text = existing["text"] or text
            if existing["text"] and text and text != existing["text"]:
                combined_text = existing["text"] + "\n\n" + text
            combined_url = existing["source_url"] or source_url
            combined_hint = existing.get("wa_title_hint", "") or wa_title_hint

            # Acumular imágenes en la lista (sin superar el máximo)
            combined_media = list(existing.get("media_list", []))
            if media_payload and len(combined_media) < _MAX_IMAGES:
                combined_media.append(media_payload)

            existing.update({
                "text": combined_text,
                "media_list": combined_media,
                "source_url": combined_url,
                "ts": time.time(),
                "wa_title_hint": combined_hint,
            })

            has_text = bool(combined_text and combined_text.strip())
            has_media = bool(combined_media)
            if has_text and has_media:
                # Espera breve para capturar imágenes adicionales del mismo envío
                delay = _BUFFER_QUICK_SECS
                log.info("WA buffer: texto+imagen(s) listos, esperando %ds para más imágenes (%s)", delay, buf_key)
            else:
                delay = _BUFFER_SECS

            timer = threading.Timer(delay, _flush_wa_buffer, args=[buf_key])
            timer.daemon = True
            existing["timer"] = timer
            timer.start()
        else:
            timer = threading.Timer(_BUFFER_SECS, _flush_wa_buffer, args=[buf_key])
            timer.daemon = True
            _wa_buffers[buf_key] = {
                "text": text,
                "media_list": [media_payload] if media_payload else [],
                "source_url": source_url,
                "jid": jid,
                "instance_name": instance_name,
                "timer": timer,
                "ts": time.time(),
                "wa_title_hint": wa_title_hint,
            }
            timer.start()
            log.info("WA buffer: iniciado para %s (espera %ds más mensajes)", buf_key, _BUFFER_SECS)


# ── Procesamiento del webhook ──────────────────────────────────────────────────

def _process_wa_message(payload: dict):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        from app.services.whatsapp_service import parse_incoming, get_media_base64
        msg = parse_incoming(payload)
        if not msg:
            return

        # Enrutar por nombre de instancia
        instance_name = payload.get("instance", "")
        if instance_name:
            s = db.query(WhatsAppSettings).filter(WhatsAppSettings.instance_name == instance_name).first()
        else:
            s = db.query(WhatsAppSettings).first()

        if not s or not s.enabled:
            return

        authorized = [re.sub(r"[^0-9]", "", n) for n in (s.authorized_numbers or "").split(",") if n.strip()]
        if authorized and msg["from"] not in authorized:
            log.info("WA: número no autorizado %s (autorizados: %s)", msg["from"], authorized)
            _log_db(db, "WARNING", f"[WA] Mensaje de número no autorizado: {msg['from']} (autorizados: {authorized})")
            return
        log.info("WA: número autorizado %s — procesando mensaje tipo=%s", msg["from"], msg.get("type"))
        _log_db(db, "INFO", f"[WA] Mensaje recibido de {msg['from']} — tipo: {msg.get('type')}")

        if msg["is_group"]:
            log.info("WA: mensaje de grupo ignorado (solo se procesan DMs)")
            return

        text = msg.get("text", "").strip()
        text, wa_title_hint = _preprocess_wa_text(text)
        media_data = None
        audio_transcript = ""

        raw_data = msg.get("_raw_data", {})
        media_dict = msg.get("media") or {}
        msg_type = msg["type"]

        import mimetypes as _mt

        if msg_type == "image":
            result = get_media_base64(s.evolution_api_url, s.evolution_api_key, s.instance_name, raw_data) if raw_data else None
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
                            ocr = _WA_BOLD_INLINE_RE.sub(r'\1', ocr)
                            text = (text + "\n\n" + ocr).strip() if text else ocr
                            log.info("WA: OCR extraído (%d chars)", len(ocr))
            else:
                log.warning("WA: no se pudo descargar la imagen — se continúa sin foto")

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
            _publish_whatsapp_news(db, s, audio_transcript, [], None, sender_jid=msg["jid"])
            return

        final_text = text
        if not final_text and not media_data:
            log.info("WA: mensaje sin contenido procesable (%s)", msg_type)
            return

        url_match = _URL_RE.search(final_text) if final_text else None
        source_url = url_match.group().rstrip(".,;)>") if url_match else None

        if not source_url and not media_data and msg_type == "text" and len(final_text) < 80:
            log.info("WA: mensaje de texto muy corto sin URL (%d chars) — ignorado", len(final_text))
            _log_db(db, "WARNING", f"[WA] Mensaje ignorado: texto muy corto ({len(final_text)} chars) y sin URL")
            return

        if source_url:
            _publish_whatsapp_news(db, s, final_text,
                                   [media_data] if media_data else [], source_url,
                                   sender_jid=msg["jid"], wa_title_hint=wa_title_hint)
            return

        buf_key = f"{instance_name}:{msg['from']}"
        _buffer_wa_content(buf_key, msg["jid"], final_text, media_data, source_url,
                           instance_name, wa_title_hint=wa_title_hint)

    except Exception as exc:
        log.error("_process_wa_message error: %s", exc)
        try:
            _log_db(db, "ERROR", f"[WA] Error procesando mensaje: {exc}")
        except Exception:
            pass
    finally:
        db.close()


def _publish_whatsapp_news(db, settings, text: str, media_list: list, source_url: str | None, sender_jid: str | None = None, wa_title_hint: str = ""):
    from app.models import GroqSettings
    from app.crypto import decrypt_value

    groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
    if not groq_cfg:
        log.warning("WA: no hay configuración de IA activa")
        return

    api_key = decrypt_value(groq_cfg.encrypted_api_key)

    from app.models import WordPressSettings as _WPSettings
    from app.worker import _fetch_wp_category_names
    wp_all = db.query(_WPSettings).filter(_WPSettings.is_active == True).all()
    # Filtrar WP sites según la asignación de la cuenta WA
    if settings.wordpress_settings_id:
        wp_sites_for_cats = [w for w in wp_all if w.id == settings.wordpress_settings_id]
    else:
        wp_sites_for_cats = wp_all
    wp_categories = _fetch_wp_category_names(wp_sites_for_cats)

    article_body = text
    scraped_image_url = None

    if source_url:
        log.info("WA: URL detectada — scrapeando %s", source_url)
        try:
            from app.services.rss_service import scrape_full_article
            scraped_text, scraped_image_url, _, _ = scrape_full_article(source_url)
            if scraped_text and len(scraped_text) > 200:
                article_body = _clean_scrape_noise(_sanitize_text(scraped_text))
                log.info("WA: artículo scrapeado y limpio (%d chars)", len(article_body))
            else:
                log.warning("WA: scraping insuficiente (%d chars)", len(scraped_text or ""))
                article_body = ""
        except Exception as exc:
            log.warning("WA: no se pudo scrapear %s: %s", source_url, exc)
            article_body = ""

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

    if source_url:
        subject = ""
    else:
        if wa_title_hint and len(wa_title_hint) > 15:
            subject = wa_title_hint[:120]
            log.info("WA: usando título en negrita del mensaje como referencia: %s", subject)
        else:
            first_line = next(
                (l.strip() for l in article_body.splitlines()
                 if len(l.strip()) > 20 and not l.strip().lower().startswith(("la imagen", "el texto", "en la imagen"))),
                ""
            )
            subject = first_line[:120] if first_line else (article_body[:100] if article_body else "Noticia por WhatsApp")

    rewrite_mode = getattr(settings, "rewrite_mode", "rewrite") or "rewrite"

    if rewrite_mode == "title_only":
        from app.services.groq_service import generate_title_for_content, _text_to_html_paragraphs
        log.info("WA: modo título potente — IA genera metadatos, contenido original intacto")
        ai_result = generate_title_for_content(
            api_key, groq_cfg.model, groq_cfg.base_prompt,
            article_body,
            available_categories=wp_categories or None,
            provider=groq_cfg.provider,
            api_base_url=groq_cfg.api_base_url,
            title_hint=wa_title_hint,
        )
        ai_result["content"] = _text_to_html_paragraphs(article_body)
    elif source_url and len(article_body) > 300:
        from app.services.groq_service import process_rss_with_groq
        ai_result = process_rss_with_groq(
            api_key, groq_cfg.model, groq_cfg.base_prompt,
            subject, article_body,
            available_categories=wp_categories or None,
            provider=groq_cfg.provider,
            api_base_url=groq_cfg.api_base_url,
        )
    else:
        from app.services.groq_service import process_email_with_groq
        ai_result = process_email_with_groq(
            api_key, groq_cfg.model, groq_cfg.base_prompt,
            subject, article_body,
            available_categories=wp_categories or None,
            provider=groq_cfg.provider,
            api_base_url=groq_cfg.api_base_url,
        )

    titulo = ai_result.get("title", "")[:80]
    log.info("WA: IA generó — %s", titulo)
    _log_db(db, "INFO", f"[WA] IA procesó: {titulo}")

    publish_mode = getattr(settings, "publish_mode", "both") or "both"

    featured_media = media_list[0] if media_list else None
    extra_media = media_list[1:] if len(media_list) > 1 else None

    if publish_mode in ("both", "wordpress_only"):
        try:
            from app.worker import _publish_ai_result
            wp_sites = wp_sites_for_cats
            if wp_sites:
                if extra_media:
                    log.info("WA → WordPress: %d imagen(es) adicionales se incluirán en la nota", len(extra_media))
                count = _publish_ai_result(db, ai_result, wp_sites,
                                           image_url=scraped_image_url, source_name="WhatsApp",
                                           image_bytes_payload=featured_media,
                                           extra_image_payloads=extra_media,
                                           instagram_settings_id=settings.instagram_settings_id)
                log.info("WA → WordPress: publicado en %d sitio(s)", count)
                if count > 0:
                    _log_db(db, "INFO", f"[WA] Publicado en WordPress: {titulo}")
                else:
                    _log_db(db, "WARNING", f"[WA] No se pudo publicar en WordPress: {titulo}")
            else:
                log.info("WA → WordPress: sin sitios activos configurados")
                _log_db(db, "WARNING", "[WA] No hay sitios WordPress activos configurados")
        except Exception as _exc:
            log.warning("WA → WordPress error: %s", _exc)
            _log_db(db, "ERROR", f"[WA] Error publicando en WordPress: {_exc}")
    else:
        log.info("WA → WordPress: omitido (publish_mode=%s)", publish_mode)

    if publish_mode in ("both", "whatsapp_only"):
        _broadcast_whatsapp(db, settings, ai_result, featured_media, scraped_image_url)
    else:
        log.info("WA broadcast: omitido (publish_mode=%s)", publish_mode)


def _build_broadcast_text(title: str, summary: str, content_html: str, post_url: str = "") -> str:
    """Construye el texto de difusión: título + resumen + CTA. Sin repetir el título."""
    title = _sanitize_text(re.sub(r'\s+', ' ', title).strip())
    summary = _sanitize_text(summary.strip()) if summary else ""

    # Extraer un extracto del cuerpo que NO sea el título ni el resumen
    excerpt = ""
    if content_html:
        plain = _html_to_plain(content_html, max_chars=800)
        title_lower = re.sub(r'\W+', ' ', title).strip().lower()
        summary_lower = re.sub(r'\W+', ' ', summary).strip().lower()[:60]
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', plain) if len(p.strip()) > 40]
        for p in paragraphs:
            p_lower = re.sub(r'\W+', ' ', p).strip().lower()
            # Saltar párrafos que son básicamente el título o el resumen
            if title_lower and p_lower.startswith(title_lower[:30]):
                continue
            if summary_lower and p_lower.startswith(summary_lower[:30]):
                continue
            excerpt = p[:350].strip()
            break

    parts = [f"*{title}*"]
    if summary:
        parts.append(summary[:350])
    if excerpt and excerpt != summary[:len(excerpt)]:
        parts.append(excerpt)

    if post_url:
        parts.append(f"📰 Ingresá y mirá la noticia completa:\n{post_url}")
    else:
        parts.append("📰 Ingresá y mirá la noticia completa presionando en la imagen o en el link de nuestro perfil.")

    return "\n\n".join(parts)


def _broadcast_whatsapp(
    db, settings, ai_result: dict,
    img_payload=None,
    fallback_image_url: str | None = None,
    post_url: str = "",
):
    if not settings.broadcast_enabled:
        log.info("WA broadcast: difusión deshabilitada")
        _log_db(db, "WARNING", "[WA] Broadcast deshabilitado — activalo en Configuración → WhatsApp")
        return

    groups = db.query(WhatsAppGroup).filter(
        WhatsAppGroup.enabled == True,
        WhatsAppGroup.whatsapp_settings_id == settings.id,
    ).all()
    channels = db.query(WhatsAppChannel).filter(
        WhatsAppChannel.enabled == True,
        WhatsAppChannel.whatsapp_settings_id == settings.id,
    ).all()

    if not groups and not channels:
        log.info("WA broadcast: sin grupos ni canales activos para esta cuenta")
        _log_db(db, "WARNING", "[WA] Sin grupos ni canales activos — configuralos en WhatsApp")
        return

    from app.services.whatsapp_service import (
        send_text, send_image_base64, send_image,
        send_to_newsletter, send_image_to_newsletter,
    )

    title = ai_result.get("title", "")
    summary = ai_result.get("summary", "")
    content_html = ai_result.get("content", "")
    msg_text = _build_broadcast_text(title, summary, content_html, post_url)

    # Descargar imagen de fallback si no hay payload directo
    scraped_img_bytes: bytes | None = None
    scraped_img_mime: str = "image/jpeg"
    if fallback_image_url and not img_payload:
        try:
            import httpx as _httpx
            ir = _httpx.get(
                fallback_image_url, timeout=15, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            ir.raise_for_status()
            scraped_img_bytes = ir.content
            scraped_img_mime = ir.headers.get("content-type", "image/jpeg").split(";")[0]
        except Exception as _exc:
            log.warning("WA: no se pudo descargar imagen %s: %s", fallback_image_url, _exc)

    # ── Envío a grupos ────────────────────────────────────────────────────────
    if groups:
        log.info("WA broadcast: enviando a %d grupo(s) — %s", len(groups), title[:60])
        _log_db(db, "INFO", f"[WA] Difundiendo a {len(groups)} grupo(s): {title[:60]}")
        for g in groups:
            sent = False
            if img_payload:
                img_bytes, _, img_mime = img_payload
                sent = send_image_base64(settings.evolution_api_url, settings.evolution_api_key,
                                         settings.instance_name, g.jid, img_bytes, img_mime, msg_text)
            if not sent and scraped_img_bytes:
                sent = send_image_base64(settings.evolution_api_url, settings.evolution_api_key,
                                         settings.instance_name, g.jid, scraped_img_bytes, scraped_img_mime, msg_text)
            if not sent and fallback_image_url:
                sent = send_image(settings.evolution_api_url, settings.evolution_api_key,
                                  settings.instance_name, g.jid, fallback_image_url, msg_text)
            if not sent:
                sent = send_text(settings.evolution_api_url, settings.evolution_api_key,
                                 settings.instance_name, g.jid, msg_text)
            log.info("WA broadcast → grupo %s: %s", g.name, "OK" if sent else "ERROR")
            if not sent:
                _log_db(db, "ERROR", f"[WA] Falló envío al grupo {g.name}")

    # ── Envío a canales (newsletter) ──────────────────────────────────────────
    if channels:
        log.info("WA broadcast: enviando a %d canal(es)", len(channels))
        _log_db(db, "INFO", f"[WA] Difundiendo a {len(channels)} canal(es): {title[:60]}")
        for ch in channels:
            sent = False
            if img_payload:
                img_bytes, _, img_mime = img_payload
                sent = send_image_to_newsletter(settings.evolution_api_url, settings.evolution_api_key,
                                                settings.instance_name, ch.jid, img_bytes, img_mime, msg_text)
            if not sent and scraped_img_bytes:
                sent = send_image_to_newsletter(settings.evolution_api_url, settings.evolution_api_key,
                                                settings.instance_name, ch.jid, scraped_img_bytes, scraped_img_mime, msg_text)
            if not sent:
                sent = send_to_newsletter(settings.evolution_api_url, settings.evolution_api_key,
                                          settings.instance_name, ch.jid, msg_text)
            log.info("WA broadcast → canal %s: %s", ch.name, "OK" if sent else "ERROR")
            if not sent:
                _log_db(db, "ERROR", f"[WA] Falló envío al canal {ch.name}")
