from __future__ import annotations

import logging
import os
import shutil
import urllib.parse
from typing import Optional

import requests as http_requests
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.crypto import decrypt_value, encrypt_value, mask_value
from app.database import get_db
from app.models import InstagramSettings
from app.services.instagram_service import refresh_token, test_connection, token_expires_at

log = logging.getLogger(__name__)

OAUTH_SCOPES = "instagram_content_publish"
GRAPH_BASE = "https://graph.facebook.com/v19.0"

router = APIRouter(prefix="/settings/instagram")
templates = Jinja2Templates(directory="app/templates")

LOGO_DIR = os.path.join("app", "static", "uploads", "logos")
os.makedirs(LOGO_DIR, exist_ok=True)


def _require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return None
    return user


@router.get("", response_class=HTMLResponse)
async def instagram_page(request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/", status_code=302)

    try:
        cfg = db.query(InstagramSettings).first()
    except Exception as exc:
        log.error("Error cargando configuración de Instagram: %s", exc, exc_info=True)
        cfg = None

    return templates.TemplateResponse(
        "settings_instagram.html",
        {"request": request, "user": user, "cfg": cfg, "mask": mask_value},
    )


@router.post("/save")
async def save_instagram(
    request: Request,
    name: str = Form("Instagram"),
    ig_user_id: str = Form(""),
    app_id: str = Form(""),
    app_secret: str = Form(""),
    access_token: str = Form(""),
    logo_position: str = Form("bottom-right"),
    max_posts_per_day: int = Form(10),
    logo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/", status_code=302)

    try:
        cfg = db.query(InstagramSettings).first()
        if not cfg:
            cfg = InstagramSettings()
            db.add(cfg)
            db.flush()  # genera el id para usar en el nombre del logo

        cfg.name = name.strip() or "Instagram"
        cfg.ig_user_id = ig_user_id.strip() or None
        cfg.app_id = app_id.strip() or None
        cfg.max_posts_per_day = max(1, min(25, max_posts_per_day))
        cfg.logo_position = logo_position if logo_position in ("top-left", "top-right", "bottom-left", "bottom-right") else "bottom-right"

        if app_secret.strip():
            cfg.encrypted_app_secret = encrypt_value(app_secret.strip())
        if access_token.strip():
            cfg.encrypted_access_token = encrypt_value(access_token.strip())
            try:
                if cfg.app_id and cfg.encrypted_app_secret:
                    secret = decrypt_value(cfg.encrypted_app_secret)
                    cfg.token_expires_at = token_expires_at(cfg.app_id, secret, access_token.strip())
            except Exception:
                pass

        if logo and logo.filename:
            try:
                ext = os.path.splitext(logo.filename)[1].lower()
                if ext in (".png", ".jpg", ".jpeg", ".webp"):
                    logo_filename = f"ig_logo_{cfg.id}{ext}"
                    logo_path = os.path.join(LOGO_DIR, logo_filename)
                    with open(logo_path, "wb") as f:
                        shutil.copyfileobj(logo.file, f)
                    cfg.logo_path = logo_path
            except Exception as logo_exc:
                log.warning("No se pudo guardar el logo: %s", logo_exc)

        db.commit()
        return RedirectResponse("/settings/instagram?msg=Configuracion+guardada", status_code=302)

    except Exception as exc:
        log.error("Error guardando configuración de Instagram: %s", exc, exc_info=True)
        db.rollback()
        import urllib.parse
        err_msg = urllib.parse.quote(str(exc)[:200])
        return RedirectResponse(f"/settings/instagram?err={err_msg}", status_code=302)


@router.post("/toggle")
async def toggle_instagram(request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = db.query(InstagramSettings).first()
    if not cfg:
        return JSONResponse({"error": "no configurado"}, status_code=404)
    cfg.is_active = not cfg.is_active
    db.commit()
    return JSONResponse({"active": cfg.is_active})


@router.post("/test")
async def test_instagram(request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = db.query(InstagramSettings).first()
    if not cfg or not cfg.ig_user_id or not cfg.encrypted_access_token:
        return JSONResponse({"ok": False, "error": "Completá todos los campos antes de probar"})
    token = decrypt_value(cfg.encrypted_access_token)
    result = test_connection(cfg.ig_user_id, token)
    return JSONResponse(result)


@router.post("/refresh-token")
async def do_refresh_token(request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = db.query(InstagramSettings).first()
    if not cfg or not cfg.encrypted_access_token or not cfg.app_id or not cfg.encrypted_app_secret:
        return JSONResponse({"ok": False, "error": "Faltan credenciales para renovar el token"})
    token = decrypt_value(cfg.encrypted_access_token)
    secret = decrypt_value(cfg.encrypted_app_secret)
    result = refresh_token(cfg.app_id, secret, token)
    if result["ok"]:
        cfg.encrypted_access_token = encrypt_value(result["access_token"])
        cfg.token_expires_at = token_expires_at(cfg.app_id, secret, result["access_token"])
        db.commit()
    return JSONResponse(result)


def _oauth_callback_url(request: Request) -> str:
    """Construye la URL de callback OAuth forzando HTTPS (requerido por Meta)."""
    base = str(request.base_url).rstrip("/")
    # Forzar HTTPS: nginx termina SSL y el app recibe HTTP internamente
    if base.startswith("http://") and not base.startswith("http://localhost"):
        base = "https://" + base[len("http://"):]
    return f"{base}/settings/instagram/oauth-callback"


@router.get("/oauth-start")
async def oauth_start(request: Request, db: Session = Depends(get_db)):
    """Inicia el flujo OAuth con Meta para obtener el token de Instagram."""
    if not _require_admin(request, db):
        return RedirectResponse("/", status_code=302)
    cfg = db.query(InstagramSettings).first()
    if not cfg or not cfg.app_id or not cfg.encrypted_app_secret:
        return RedirectResponse(
            "/settings/instagram?err=Primero+guarda+el+App+ID+y+App+Secret", status_code=302
        )
    callback = _oauth_callback_url(request)
    url = (
        f"https://www.facebook.com/dialog/oauth"
        f"?client_id={cfg.app_id}"
        f"&redirect_uri={urllib.parse.quote(callback, safe='')}"
        f"&scope={OAUTH_SCOPES}"
        f"&response_type=code"
    )
    return RedirectResponse(url, status_code=302)


@router.get("/oauth-callback")
async def oauth_callback(
    request: Request,
    code: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Recibe el código OAuth de Meta, lo intercambia por un long-lived token y lo guarda."""
    if not _require_admin(request, db):
        return RedirectResponse("/", status_code=302)

    if error or not code:
        msg = urllib.parse.quote(f"OAuth cancelado o error: {error or 'sin codigo'}")
        return RedirectResponse(f"/settings/instagram?err={msg}", status_code=302)

    cfg = db.query(InstagramSettings).first()
    if not cfg or not cfg.app_id or not cfg.encrypted_app_secret:
        return RedirectResponse(
            "/settings/instagram?err=Configuracion+incompleta", status_code=302
        )

    try:
        app_secret = decrypt_value(cfg.encrypted_app_secret)
        callback = _oauth_callback_url(request)

        # 1) Intercambiar code por token corto
        r = http_requests.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "client_id": cfg.app_id,
                "redirect_uri": callback,
                "client_secret": app_secret,
                "code": code,
            },
            timeout=30,
        )
        data = r.json()
        if "error" in data:
            msg = urllib.parse.quote(data["error"].get("message", "Error")[:200])
            return RedirectResponse(f"/settings/instagram?err={msg}", status_code=302)

        short_token = data.get("access_token", "")
        if not short_token:
            return RedirectResponse(
                "/settings/instagram?err=No+se+recibio+token+de+Meta", status_code=302
            )

        # 2) Intercambiar por long-lived (~60 días)
        r2 = http_requests.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": cfg.app_id,
                "client_secret": app_secret,
                "fb_exchange_token": short_token,
            },
            timeout=30,
        )
        data2 = r2.json()
        long_token = data2.get("access_token") if "error" not in data2 else short_token

        cfg.encrypted_access_token = encrypt_value(long_token)
        try:
            cfg.token_expires_at = token_expires_at(cfg.app_id, app_secret, long_token)
        except Exception:
            pass
        db.commit()

        return RedirectResponse(
            "/settings/instagram?msg=Token+obtenido+y+guardado+correctamente", status_code=302
        )

    except Exception as exc:
        log.error("OAuth callback error: %s", exc, exc_info=True)
        msg = urllib.parse.quote(str(exc)[:200])
        return RedirectResponse(f"/settings/instagram?err={msg}", status_code=302)
