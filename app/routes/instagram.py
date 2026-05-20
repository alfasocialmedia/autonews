from __future__ import annotations

import logging
import os
import shutil
from typing import Optional

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
