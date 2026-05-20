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

OAUTH_SCOPES = "instagram_content_publish,pages_show_list,pages_read_engagement"
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


@router.post("/fetch-ig-id")
async def fetch_ig_id(request: Request, db: Session = Depends(get_db)):
    """Busca el Instagram Business Account ID real usando el token guardado.
    Estrategia 1: GET /me/accounts (requiere pages_show_list — nuevo token con scopes ampliados).
    Estrategia 2: GET /me (funciona con solo instagram_content_publish).
    """
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    cfg = db.query(InstagramSettings).first()
    if not cfg or not cfg.encrypted_access_token:
        return JSONResponse({"ok": False, "error": "No hay token guardado — conectá con Meta primero"})

    token = decrypt_value(cfg.encrypted_access_token)

    try:
        # Paso 0: verificar que el token sea válido y obtener el usuario de Facebook
        r_me = http_requests.get(
            f"{GRAPH_BASE}/me",
            params={"fields": "id,name", "access_token": token},
            timeout=20,
        )
        me_basic = r_me.json()
        if "error" in me_basic:
            return JSONResponse({"ok": False, "error": f"Token inválido o expirado: {me_basic['error'].get('message', 'Error desconocido')}"})
        fb_user_name = me_basic.get("name", "")

        # Paso 1: listar páginas de Facebook y sus cuentas IG vinculadas
        r = http_requests.get(
            f"{GRAPH_BASE}/me/accounts",
            params={"access_token": token, "fields": "id,name,instagram_business_account{id,username,name}"},
            timeout=20,
        )
        data = r.json()

        if "error" in data:
            return JSONResponse({
                "ok": False,
                "need_reauth": True,
                "fb_user": fb_user_name,
                "error": "El token no tiene permiso para listar páginas. Hacé clic en 'Conectar con Meta' para renovar el token y volvé a intentar.",
            })

        pages = data.get("data", [])
        accounts = []
        for page in pages:
            ig_biz = (page.get("instagram_business_account") or {})
            ig_id = ig_biz.get("id", "")
            if ig_id:
                accounts.append({
                    "ig_id": ig_id,
                    "username": ig_biz.get("username", ""),
                    "name": ig_biz.get("name", ""),
                    "page_name": page.get("name", ""),
                })

        if accounts:
            return JSONResponse({"ok": True, "accounts": accounts})

        if pages:
            page_names = ", ".join(p.get("name", "") for p in pages)
            return JSONResponse({
                "ok": False,
                "no_ig_linked": True,
                "fb_user": fb_user_name,
                "page_names": page_names,
                "error": f"Páginas encontradas: {page_names} — pero ninguna tiene una cuenta de Instagram Business vinculada.",
            })

        # Sin páginas: el usuario de Facebook autenticado no administra ninguna Página
        return JSONResponse({
            "ok": False,
            "no_pages": True,
            "fb_user": fb_user_name,
            "error": f"La cuenta de Facebook '{fb_user_name}' no administra ninguna Página de Facebook.",
        })

    except Exception as exc:
        log.error("Error en fetch_ig_id: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)[:300]})


@router.post("/test-publish")
async def test_publish_instagram(request: Request, db: Session = Depends(get_db)):
    """Publica una imagen de prueba real en Instagram para verificar el flujo completo."""
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    cfg = db.query(InstagramSettings).first()
    if not cfg or not cfg.ig_user_id or not cfg.encrypted_access_token:
        return JSONResponse({"ok": False, "error": "Configurá Instagram (ID de cuenta y token) antes de publicar"})
    if not cfg.is_active:
        return JSONResponse({"ok": False, "error": "Instagram está inactivo — activalo antes de publicar"})

    try:
        import urllib.request as _ur
        from app.models import Log, WordPressSettings
        from app.services.image_template_service import build_instagram_image
        from app.services.instagram_service import publish_image as ig_publish
        from app.services.wordpress_service import upload_media

        test_title = "Prueba de publicación automática — AutoNews"
        img_url = (
            "https://image.pollinations.ai/prompt/professional%20news%20photo%20editorial%20style"
            "?width=1200&height=630&seed=42&nologo=true&model=flux"
        )

        try:
            req = _ur.Request(img_url, headers={"User-Agent": "AutoNews/1.0"})
            with _ur.urlopen(req, timeout=40) as r:
                img_bytes = r.read()
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"No se pudo descargar imagen de prueba: {exc}"})

        ig_bytes = build_instagram_image(
            img_bytes,
            test_title,
            logo_path=cfg.logo_path,
            logo_position=cfg.logo_position or "bottom-right",
        )

        wp = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).first()
        if not wp:
            return JSONResponse({"ok": False, "error": "No hay sitio WordPress activo para hospedar la imagen"})

        wp_pwd = decrypt_value(wp.encrypted_app_password)
        media_result = upload_media(wp.site_url, wp.api_user, wp_pwd, ig_bytes, "ig_test.jpg", "image/jpeg")
        if not media_result:
            return JSONResponse({"ok": False, "error": "No se pudo subir la imagen de prueba a WordPress"})

        _, public_url = media_result
        token = decrypt_value(cfg.encrypted_access_token)
        caption = "🔧 Prueba de publicación automática — AutoNews\n\n#autonews #prueba #test"

        result = ig_publish(cfg.ig_user_id, token, public_url, caption)

        if result["ok"]:
            db.add(Log(level="INFO", message="[Instagram] Prueba publicada correctamente", source="instagram"))
            db.commit()

        return JSONResponse(result)

    except Exception as exc:
        log.error("Error en test_publish_instagram: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)[:300]})


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


def _popup_response(ok: bool, message: str) -> HTMLResponse:
    """Devuelve HTML que cierra el popup y actualiza la ventana padre."""
    escaped = message.replace("'", "\\'").replace("\n", " ")
    if ok:
        script = (
            "if(window.opener&&!window.opener.closed){"
            f"window.opener.location.href='/settings/instagram?msg={urllib.parse.quote(message)}';"
            "setTimeout(()=>window.close(),300);"
            "}else{"
            f"window.location.href='/settings/instagram?msg={urllib.parse.quote(message)}';"
            "}"
        )
        body_html = f"<p style='color:green'>✓ {escaped}</p>"
    else:
        script = (
            "if(window.opener&&!window.opener.closed){"
            f"window.opener.location.href='/settings/instagram?err={urllib.parse.quote(message)}';"
            "setTimeout(()=>window.close(),300);"
            "}else{"
            f"window.location.href='/settings/instagram?err={urllib.parse.quote(message)}';"
            "}"
        )
        body_html = f"<p style='color:red'>✗ {escaped}</p>"

    return HTMLResponse(
        f"<!doctype html><html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
        f"{body_html}<p style='color:#888;font-size:.9em'>Cerrando...</p>"
        f"<script>{script}</script></body></html>"
    )


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
        return _popup_response(False, f"OAuth cancelado: {error or 'sin codigo'}")

    cfg = db.query(InstagramSettings).first()
    if not cfg or not cfg.app_id or not cfg.encrypted_app_secret:
        return _popup_response(False, "Configuracion incompleta: falta App ID o App Secret")

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
            meta_err = data["error"].get("message", "Error de Meta")
            hint = " — verificá que el App Secret sea correcto" if "client secret" in meta_err.lower() else ""
            return _popup_response(False, meta_err + hint)

        short_token = data.get("access_token", "")
        if not short_token:
            return _popup_response(False, "Meta no devolvio el token")

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

        return _popup_response(True, "Token obtenido y guardado correctamente")

    except Exception as exc:
        log.error("OAuth callback error: %s", exc, exc_info=True)
        return _popup_response(False, str(exc)[:200])
