from __future__ import annotations

import logging
import os
import pathlib
import shutil
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import requests as http_requests
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.crypto import decrypt_value, encrypt_value, mask_value
from app.database import get_db
from app.models import InstagramSettings
from app.services.instagram_service import refresh_token, test_connection, token_expires_at

log = logging.getLogger(__name__)

OAUTH_SCOPES = "instagram_business_basic,instagram_content_publish"
IG_OAUTH_URL   = "https://www.instagram.com/oauth/authorize"
IG_TOKEN_URL   = "https://api.instagram.com/oauth/access_token"
IG_GRAPH_BASE  = "https://graph.instagram.com"
GRAPH_BASE     = "https://graph.facebook.com/v19.0"

router = APIRouter(prefix="/settings/instagram")
templates = Jinja2Templates(directory="app/templates")

def _logo_dir() -> str:
    """Returns persistent logo storage dir: /app/data/logos in Docker, app/static/uploads/logos in dev."""
    pdir = pathlib.Path("/app/data/logos")
    if pdir.parent.exists():
        pdir.mkdir(exist_ok=True)
        return str(pdir)
    ldir = pathlib.Path("app/static/uploads/logos")
    ldir.mkdir(parents=True, exist_ok=True)
    return str(ldir)


def _require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return None
    return user


def _get_account(db: Session, account_id: int) -> InstagramSettings | None:
    return db.query(InstagramSettings).filter(InstagramSettings.id == account_id).first()


# ─── Lista de cuentas ────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def instagram_list(request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/", status_code=302)
    accounts = db.query(InstagramSettings).order_by(InstagramSettings.id).all()
    return templates.TemplateResponse(
        "settings_instagram_list.html",
        {"request": request, "user": user, "accounts": accounts},
    )


# ─── Nueva cuenta ────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def instagram_new_page(request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        "settings_instagram_edit.html",
        {"request": request, "user": user, "cfg": None, "mask": mask_value},
    )


@router.post("/new")
async def instagram_create(
    request: Request,
    name: str = Form("Instagram"),
    ig_user_id: str = Form(""),
    app_id: str = Form(""),
    app_secret: str = Form(""),
    access_token: str = Form(""),
    logo_position: str = Form("bottom-right"),
    max_posts_per_day: int = Form(10),
    gradient_color: str = Form("#000000"),
    gradient_opacity: int = Form(200),
    gradient_height: int = Form(480),
    font_size: int = Form(62),
    text_color: str = Form("#ffffff"),
    banner_text: str = Form(""),
    banner_color: str = Form("#e53935"),
    banner_text_color: str = Form("#ffffff"),
    text_align: str = Form("left"),
    title_y_offset: int = Form(0),
    font_family: str = Form("Montserrat"),
    font_weight: str = Form("bold"),
    text_bg_color: str = Form("#000000"),
    text_bg_opacity: int = Form(0),
    logo_size: int = Form(180),
    banner_style: str = Form("pill"),
    banner_font_weight: str = Form("bold"),
    banner_y_offset: int = Form(0),
    banner_align: str = Form("center"),
    text_bg_padding_x: int = Form(0),
    text_bg_padding_y: int = Form(18),
    text_bg_full_width: str = Form("on"),
    title_max_lines: int = Form(4),
    show_category: str = Form(""),
    category_bg_color: str = Form("#e53935"),
    category_text_color: str = Form("#ffffff"),
    category_x_percent: int = Form(0),
    category_y_percent: int = Form(0),
    banner_font_family: str = Form("Montserrat"),
    category_font_family: str = Form("Montserrat"),
    text_box_x_pct: int = Form(0),
    text_box_y_pct: int = Form(70),
    text_box_w_pct: int = Form(100),
    text_bg_border_radius: int = Form(0),
    text_bg_border_width: int = Form(0),
    text_bg_border_color: str = Form("#ffffff"),
    text_bg_height_pct: int = Form(0),
    banner_border_radius: str = Form(""),
    banner_border_width: int = Form(0),
    banner_border_color: str = Form("#ffffff"),
    banner_full_width: str = Form(""),
    text_bg_fill_to_bottom: str = Form(""),
    title_shadow: str = Form("on"),
    ig_caption_prompt: str = Form(""),
    logo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/", status_code=302)

    cfg = InstagramSettings(name=name.strip() or "Instagram")
    db.add(cfg)
    db.flush()

    _apply_form_to_cfg(cfg, ig_user_id, app_id, app_secret, access_token,
                       logo_position, max_posts_per_day,
                       gradient_color, gradient_opacity, gradient_height,
                       font_size, text_color, banner_text, banner_color, banner_text_color,
                       text_align, title_y_offset, font_family, font_weight,
                       text_bg_color, text_bg_opacity, logo_size,
                       banner_style, banner_font_weight, banner_y_offset, banner_align,
                       text_bg_padding_x, text_bg_padding_y, text_bg_full_width,
                       title_max_lines, show_category, category_bg_color,
                       category_text_color, category_x_percent, category_y_percent,
                       banner_font_family, category_font_family,
                       text_box_x_pct, text_box_y_pct, text_box_w_pct,
                       text_bg_border_radius, text_bg_border_width, text_bg_border_color,
                       text_bg_height_pct, banner_border_radius, banner_border_width,
                       banner_border_color, banner_full_width,
                       text_bg_fill_to_bottom, title_shadow, ig_caption_prompt,
                       logo, db)
    db.commit()
    return RedirectResponse(f"/settings/instagram/{cfg.id}?msg=Cuenta+creada+correctamente", status_code=302)


# ─── Editar cuenta ───────────────────────────────────────────────────────────

@router.get("/{account_id}", response_class=HTMLResponse)
async def instagram_edit_page(account_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/", status_code=302)
    cfg = _get_account(db, account_id)
    if not cfg:
        return RedirectResponse("/settings/instagram", status_code=302)
    return templates.TemplateResponse(
        "settings_instagram_edit.html",
        {"request": request, "user": user, "cfg": cfg, "mask": mask_value},
    )


@router.post("/{account_id}/save")
async def instagram_save(
    account_id: int,
    request: Request,
    name: str = Form("Instagram"),
    ig_user_id: str = Form(""),
    app_id: str = Form(""),
    app_secret: str = Form(""),
    access_token: str = Form(""),
    logo_position: str = Form("bottom-right"),
    max_posts_per_day: int = Form(10),
    gradient_color: str = Form("#000000"),
    gradient_opacity: int = Form(200),
    gradient_height: int = Form(480),
    font_size: int = Form(62),
    text_color: str = Form("#ffffff"),
    banner_text: str = Form(""),
    banner_color: str = Form("#e53935"),
    banner_text_color: str = Form("#ffffff"),
    text_align: str = Form("left"),
    title_y_offset: int = Form(0),
    font_family: str = Form("Montserrat"),
    font_weight: str = Form("bold"),
    text_bg_color: str = Form("#000000"),
    text_bg_opacity: int = Form(0),
    logo_size: int = Form(180),
    banner_style: str = Form("pill"),
    banner_font_weight: str = Form("bold"),
    banner_y_offset: int = Form(0),
    banner_align: str = Form("center"),
    text_bg_padding_x: int = Form(0),
    text_bg_padding_y: int = Form(18),
    text_bg_full_width: str = Form("on"),
    title_max_lines: int = Form(4),
    show_category: str = Form(""),
    category_bg_color: str = Form("#e53935"),
    category_text_color: str = Form("#ffffff"),
    category_x_percent: int = Form(0),
    category_y_percent: int = Form(0),
    banner_font_family: str = Form("Montserrat"),
    category_font_family: str = Form("Montserrat"),
    text_box_x_pct: int = Form(0),
    text_box_y_pct: int = Form(70),
    text_box_w_pct: int = Form(100),
    text_bg_border_radius: int = Form(0),
    text_bg_border_width: int = Form(0),
    text_bg_border_color: str = Form("#ffffff"),
    text_bg_height_pct: int = Form(0),
    banner_border_radius: str = Form(""),
    banner_border_width: int = Form(0),
    banner_border_color: str = Form("#ffffff"),
    banner_full_width: str = Form(""),
    text_bg_fill_to_bottom: str = Form(""),
    title_shadow: str = Form("on"),
    ig_caption_prompt: str = Form(""),
    logo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    user = _require_admin(request, db)
    if not user:
        return RedirectResponse("/", status_code=302)

    cfg = _get_account(db, account_id)
    if not cfg:
        return RedirectResponse("/settings/instagram", status_code=302)

    try:
        cfg.name = name.strip() or "Instagram"
        _apply_form_to_cfg(cfg, ig_user_id, app_id, app_secret, access_token,
                           logo_position, max_posts_per_day,
                           gradient_color, gradient_opacity, gradient_height,
                           font_size, text_color, banner_text, banner_color, banner_text_color,
                           text_align, title_y_offset, font_family, font_weight,
                           text_bg_color, text_bg_opacity, logo_size,
                           banner_style, banner_font_weight, banner_y_offset, banner_align,
                           text_bg_padding_x, text_bg_padding_y, text_bg_full_width,
                           title_max_lines, show_category, category_bg_color,
                           category_text_color, category_x_percent, category_y_percent,
                           banner_font_family, category_font_family,
                           text_box_x_pct, text_box_y_pct, text_box_w_pct,
                           text_bg_border_radius, text_bg_border_width, text_bg_border_color,
                           text_bg_height_pct, banner_border_radius, banner_border_width,
                           banner_border_color, banner_full_width,
                           text_bg_fill_to_bottom, title_shadow, ig_caption_prompt,
                           logo, db)
        db.commit()
        return RedirectResponse(
            f"/settings/instagram/{account_id}?msg=Configuracion+guardada", status_code=302
        )
    except Exception as exc:
        log.error("Error guardando Instagram %s: %s", account_id, exc, exc_info=True)
        db.rollback()
        err = urllib.parse.quote(str(exc)[:200])
        return RedirectResponse(f"/settings/instagram/{account_id}?err={err}", status_code=302)


_VALID_FONTS = {
    "Montserrat", "Poppins", "Roboto", "Open Sans", "Lato", "Raleway",
    "Nunito", "Inter", "Playfair Display", "Merriweather", "Lora",
    "Oswald", "Bebas Neue", "Anton",
}
_VALID_WEIGHTS = {"regular", "medium", "bold", "extrabold"}
_VALID_BANNER_STYLES = {"pill", "rect", "none"}


def _apply_form_to_cfg(
    cfg: InstagramSettings,
    ig_user_id: str, app_id: str, app_secret: str, access_token: str,
    logo_position: str, max_posts_per_day: int,
    gradient_color: str, gradient_opacity: int, gradient_height: int,
    font_size: int, text_color: str,
    banner_text: str, banner_color: str, banner_text_color: str,
    text_align: str, title_y_offset: int,
    font_family: str, font_weight: str,
    text_bg_color: str, text_bg_opacity: int, logo_size: int,
    banner_style: str, banner_font_weight: str,
    banner_y_offset: int, banner_align: str,
    text_bg_padding_x: int, text_bg_padding_y: int, text_bg_full_width: str,
    title_max_lines: int, show_category: str,
    category_bg_color: str, category_text_color: str, category_x_percent: int, category_y_percent: int,
    banner_font_family: str, category_font_family: str,
    text_box_x_pct: int, text_box_y_pct: int, text_box_w_pct: int,
    text_bg_border_radius: int, text_bg_border_width: int, text_bg_border_color: str,
    text_bg_height_pct: int, banner_border_radius: str, banner_border_width: int,
    banner_border_color: str, banner_full_width: str,
    text_bg_fill_to_bottom: str, title_shadow: str, ig_caption_prompt: str,
    logo: Optional[UploadFile], db: Session,
):
    from app.services.gfonts_service import LEGACY_MAP
    cfg.ig_user_id = ig_user_id.strip() or None
    cfg.app_id = app_id.strip() or None
    cfg.max_posts_per_day = max(1, min(25, max_posts_per_day))
    cfg.logo_position = logo_position if logo_position in (
        "top-left", "top-right", "bottom-left", "bottom-right"
    ) else "bottom-right"
    cfg.logo_size = max(60, min(300, logo_size))
    cfg.gradient_color = gradient_color if gradient_color.startswith("#") else "#000000"
    cfg.gradient_opacity = max(0, min(255, gradient_opacity))
    cfg.gradient_height = max(100, min(1440, gradient_height))
    cfg.font_size = max(20, min(120, font_size))
    cfg.text_color = text_color if text_color.startswith("#") else "#ffffff"
    cfg.banner_text = banner_text.strip() or None
    cfg.banner_color = banner_color if banner_color.startswith("#") else "#e53935"
    cfg.banner_text_color = banner_text_color if banner_text_color.startswith("#") else "#ffffff"
    cfg.text_align = text_align if text_align in ("left", "center", "right") else "left"
    cfg.title_y_offset = max(-200, min(900, title_y_offset))
    resolved_family = LEGACY_MAP.get(font_family, font_family)
    cfg.font_family = resolved_family if resolved_family in _VALID_FONTS else "Montserrat"
    cfg.font_weight = font_weight if font_weight in _VALID_WEIGHTS else "bold"
    cfg.text_bg_color = text_bg_color if text_bg_color.startswith("#") else "#000000"
    cfg.text_bg_opacity = max(0, min(220, text_bg_opacity))
    cfg.banner_style = banner_style if banner_style in _VALID_BANNER_STYLES else "pill"
    cfg.banner_font_weight = banner_font_weight if banner_font_weight in _VALID_WEIGHTS else "bold"
    cfg.banner_y_offset = max(-200, min(800, banner_y_offset))
    cfg.banner_align = banner_align if banner_align in ("left", "center", "right") else "center"
    cfg.text_bg_padding_x = max(0, min(200, text_bg_padding_x))
    cfg.text_bg_padding_y = max(0, min(100, text_bg_padding_y))
    cfg.text_bg_full_width = text_bg_full_width.lower() in ("on", "true", "1", "yes")
    cfg.title_max_lines = max(1, min(6, title_max_lines))
    cfg.show_category = show_category.lower() in ("on", "true", "1", "yes")
    cfg.category_bg_color = category_bg_color if category_bg_color.startswith("#") else "#e53935"
    cfg.category_text_color = category_text_color if category_text_color.startswith("#") else "#ffffff"
    cfg.category_x_percent = max(0, min(100, category_x_percent))
    cfg.category_y_percent = max(0, min(100, category_y_percent))
    cfg.category_position = "top-right" if cfg.category_x_percent >= 50 else "top-left"
    from app.services.gfonts_service import LEGACY_MAP as _LM
    cfg.banner_font_family = _LM.get(banner_font_family, banner_font_family) if banner_font_family in _VALID_FONTS or banner_font_family in _LM else "Montserrat"
    cfg.category_font_family = _LM.get(category_font_family, category_font_family) if category_font_family in _VALID_FONTS or category_font_family in _LM else "Montserrat"
    cfg.text_box_x_pct = max(0, min(90, text_box_x_pct))
    cfg.text_box_y_pct = max(0, min(95, text_box_y_pct))
    cfg.text_box_w_pct = max(10, min(100, text_box_w_pct))
    cfg.text_bg_border_radius = max(0, min(500, text_bg_border_radius))
    cfg.text_bg_border_width = max(0, min(40, text_bg_border_width))
    cfg.text_bg_border_color = text_bg_border_color if text_bg_border_color.startswith("#") else "#ffffff"
    cfg.text_bg_height_pct = max(0, min(80, text_bg_height_pct))
    cfg.banner_border_radius = int(banner_border_radius) if banner_border_radius.strip().lstrip("-").isdigit() else None
    cfg.banner_border_width = max(0, min(20, banner_border_width))
    cfg.banner_border_color = banner_border_color if banner_border_color.startswith("#") else "#ffffff"
    cfg.banner_full_width = banner_full_width.lower() in ("on", "true", "1", "yes")
    cfg.text_bg_fill_to_bottom = text_bg_fill_to_bottom.lower() in ("on", "true", "1", "yes")
    cfg.title_shadow = title_shadow.lower() in ("on", "true", "1", "yes")
    cfg.ig_caption_prompt = ig_caption_prompt.strip() or None

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
                logo_path = os.path.join(_logo_dir(), logo_filename)
                with open(logo_path, "wb") as f:
                    shutil.copyfileobj(logo.file, f)
                cfg.logo_path = logo_path
        except Exception as logo_exc:
            log.warning("No se pudo guardar el logo: %s", logo_exc)


# ─── Eliminar cuenta ─────────────────────────────────────────────────────────

@router.post("/{account_id}/delete")
async def instagram_delete(account_id: int, request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = _get_account(db, account_id)
    if cfg:
        db.delete(cfg)
        db.commit()
    return RedirectResponse("/settings/instagram?msg=Cuenta+eliminada", status_code=302)


# ─── Toggle activo ───────────────────────────────────────────────────────────

@router.post("/{account_id}/toggle")
async def toggle_instagram(account_id: int, request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = _get_account(db, account_id)
    if not cfg:
        return JSONResponse({"error": "no encontrado"}, status_code=404)
    cfg.is_active = not cfg.is_active
    db.commit()
    return JSONResponse({"active": cfg.is_active})


# ─── Servir logo ─────────────────────────────────────────────────────────────

@router.get("/{account_id}/logo")
async def serve_logo(account_id: int, request: Request, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse, Response as FR
    if not _require_admin(request, db):
        return FR(status_code=403)
    cfg = _get_account(db, account_id)
    if not cfg or not cfg.logo_path:
        return FR(status_code=404)
    logo_path = pathlib.Path(cfg.logo_path)
    if not logo_path.exists():
        return FR(status_code=404)
    ext = logo_path.suffix.lower()
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    return FileResponse(str(logo_path), media_type=media_types.get(ext, "image/png"))


# ─── Vista previa ────────────────────────────────────────────────────────────

# Cache del fondo de preview por account_id para renders rápidos (evita re-descargar Pollinations)
_preview_bg_cache: dict[int, bytes] = {}


def _make_fallback_image_bytes(w: int = 1200, h: int = 800) -> bytes:
    """Genera un degradado azul-oscuro como imagen de fondo cuando Pollinations no responde."""
    from PIL import Image, ImageDraw
    import io as _io
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        ratio = y / h
        r = int(20 + 30 * ratio)
        g = int(30 + 40 * ratio)
        b = int(80 + 60 * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@router.get("/{account_id}/preview-image")
async def preview_image(
    account_id: int,
    request: Request,
    db: Session = Depends(get_db),
    # Query params para preview en tiempo real (sobreescriben los valores de DB)
    q_font_size: Optional[int] = Query(None, alias="font_size"),
    q_text_color: Optional[str] = Query(None, alias="text_color"),
    q_gradient_color: Optional[str] = Query(None, alias="gradient_color"),
    q_gradient_opacity: Optional[int] = Query(None, alias="gradient_opacity"),
    q_gradient_height: Optional[int] = Query(None, alias="gradient_height"),
    q_banner_text: Optional[str] = Query(None, alias="banner_text"),
    q_banner_color: Optional[str] = Query(None, alias="banner_color"),
    q_banner_text_color: Optional[str] = Query(None, alias="banner_text_color"),
    q_text_align: Optional[str] = Query(None, alias="text_align"),
    q_title_y_offset: Optional[int] = Query(None, alias="title_y_offset"),
    q_font_family: Optional[str] = Query(None, alias="font_family"),
    q_text_bg_color: Optional[str] = Query(None, alias="text_bg_color"),
    q_text_bg_opacity: Optional[int] = Query(None, alias="text_bg_opacity"),
    q_logo_size: Optional[int] = Query(None, alias="logo_size"),
    q_font_weight: Optional[str] = Query(None, alias="font_weight"),
    q_banner_style: Optional[str] = Query(None, alias="banner_style"),
    q_banner_font_weight: Optional[str] = Query(None, alias="banner_font_weight"),
    q_banner_y_offset: Optional[int] = Query(None, alias="banner_y_offset"),
    q_banner_align: Optional[str] = Query(None, alias="banner_align"),
    q_text_bg_padding_x: Optional[int] = Query(None, alias="text_bg_padding_x"),
    q_text_bg_padding_y: Optional[int] = Query(None, alias="text_bg_padding_y"),
    q_text_bg_full_width: Optional[int] = Query(None, alias="text_bg_full_width"),
    q_title_max_lines: Optional[int] = Query(None, alias="title_max_lines"),
    q_show_category: Optional[int] = Query(None, alias="show_category"),
    q_category_bg_color: Optional[str] = Query(None, alias="category_bg_color"),
    q_category_text_color: Optional[str] = Query(None, alias="category_text_color"),
    q_category_x_percent: Optional[int] = Query(None, alias="category_x_percent"),
    q_category_y_percent: Optional[int] = Query(None, alias="category_y_percent"),
    q_banner_font_family: Optional[str] = Query(None, alias="banner_font_family"),
    q_category_font_family: Optional[str] = Query(None, alias="category_font_family"),
    q_text_box_x_pct: Optional[int] = Query(None, alias="text_box_x_pct"),
    q_text_box_y_pct: Optional[int] = Query(None, alias="text_box_y_pct"),
    q_text_box_w_pct: Optional[int] = Query(None, alias="text_box_w_pct"),
    q_text_bg_border_radius: Optional[int] = Query(None, alias="text_bg_border_radius"),
    q_text_bg_border_width: Optional[int] = Query(None, alias="text_bg_border_width"),
    q_text_bg_border_color: Optional[str] = Query(None, alias="text_bg_border_color"),
    q_text_bg_height_pct: Optional[int] = Query(None, alias="text_bg_height_pct"),
    q_banner_border_radius: Optional[int] = Query(None, alias="banner_border_radius"),
    q_banner_border_width: Optional[int] = Query(None, alias="banner_border_width"),
    q_banner_border_color: Optional[str] = Query(None, alias="banner_border_color"),
    q_banner_full_width: Optional[int] = Query(None, alias="banner_full_width"),
    q_text_bg_fill_to_bottom: Optional[int] = Query(None, alias="text_bg_fill_to_bottom"),
    q_title_shadow: Optional[int] = Query(None, alias="title_shadow"),
    q_preview_title: Optional[str] = Query(None, alias="preview_title"),
):
    if not _require_admin(request, db):
        from fastapi.responses import Response
        return Response(status_code=403)

    cfg = _get_account(db, account_id)
    from fastapi.responses import Response
    import urllib.request as _ur
    from app.services.image_template_service import build_instagram_image

    # Usar fondo cacheado si existe (evita llamar Pollinations en cada render)
    img_bytes = _preview_bg_cache.get(account_id)
    if not img_bytes:
        img_url = (
            "https://image.pollinations.ai/prompt/professional%20news%20photo%20editorial%20style"
            "?width=1200&height=800&seed=7&nologo=true&model=flux"
        )
        try:
            req = _ur.Request(img_url, headers={"User-Agent": "AutoNews/1.0"})
            with _ur.urlopen(req, timeout=12) as r:
                img_bytes = r.read()
            _preview_bg_cache[account_id] = img_bytes
        except Exception:
            pass

    if not img_bytes:
        img_bytes = _make_fallback_image_bytes()
        _preview_bg_cache[account_id] = img_bytes

    # Query param tiene prioridad; si no viene, usa el valor de DB; si no hay DB, usa default
    def _eff(q_val, db_val, default):
        return q_val if q_val is not None else (db_val if db_val is not None else default)

    try:
        _preview_title = (q_preview_title.strip() if q_preview_title and q_preview_title.strip()
                         else "Vista previa — Título de la noticia de ejemplo")
        ig_bytes = build_instagram_image(
            img_bytes,
            _preview_title,
            logo_path=cfg.logo_path if cfg else None,
            logo_position=((cfg.logo_position or "bottom-right") if cfg else "bottom-right"),
            logo_size=_eff(q_logo_size, cfg.logo_size if cfg else None, 180),
            gradient_color=_eff(q_gradient_color, cfg.gradient_color if cfg else None, "#000000"),
            gradient_opacity=_eff(q_gradient_opacity, cfg.gradient_opacity if cfg else None, 200),
            gradient_height=_eff(q_gradient_height, cfg.gradient_height if cfg else None, 480),
            font_size=_eff(q_font_size, cfg.font_size if cfg else None, 62),
            text_color=_eff(q_text_color, cfg.text_color if cfg else None, "#ffffff"),
            banner_text=q_banner_text if q_banner_text is not None else (cfg.banner_text if cfg else None),
            banner_color=_eff(q_banner_color, cfg.banner_color if cfg else None, "#e53935"),
            banner_text_color=_eff(q_banner_text_color, cfg.banner_text_color if cfg else None, "#ffffff"),
            text_align=_eff(q_text_align, cfg.text_align if cfg else None, "left"),
            title_y_offset=_eff(q_title_y_offset, cfg.title_y_offset if cfg else None, 0),
            font_family=_eff(q_font_family, cfg.font_family if cfg else None, "Montserrat"),
            text_bg_color=_eff(q_text_bg_color, cfg.text_bg_color if cfg else None, "#000000"),
            text_bg_opacity=_eff(q_text_bg_opacity, cfg.text_bg_opacity if cfg else None, 0),
            font_weight=_eff(q_font_weight, cfg.font_weight if cfg else None, "bold"),
            banner_style=_eff(q_banner_style, cfg.banner_style if cfg else None, "pill"),
            banner_font_weight=_eff(q_banner_font_weight, cfg.banner_font_weight if cfg else None, "bold"),
            banner_y_offset=_eff(q_banner_y_offset, cfg.banner_y_offset if cfg else None, 0),
            banner_align=_eff(q_banner_align, cfg.banner_align if cfg else None, "center"),
            text_bg_padding_x=_eff(q_text_bg_padding_x, cfg.text_bg_padding_x if cfg else None, 0),
            text_bg_padding_y=_eff(q_text_bg_padding_y, cfg.text_bg_padding_y if cfg else None, 18),
            text_bg_full_width=bool(_eff(q_text_bg_full_width, (1 if cfg and cfg.text_bg_full_width else 0) if cfg else None, 1)),
            title_max_lines=_eff(q_title_max_lines, cfg.title_max_lines if cfg else None, 4),
            category="Categoría de ejemplo" if _eff(q_show_category, (1 if cfg and cfg.show_category else 0) if cfg else None, 0) else None,
            show_category=bool(_eff(q_show_category, (1 if cfg and cfg.show_category else 0) if cfg else None, 0)),
            category_bg_color=_eff(q_category_bg_color, cfg.category_bg_color if cfg else None, "#e53935"),
            category_text_color=_eff(q_category_text_color, cfg.category_text_color if cfg else None, "#ffffff"),
            category_x_percent=_eff(q_category_x_percent, cfg.category_x_percent if cfg else None, 0),
            category_y_percent=_eff(q_category_y_percent, cfg.category_y_percent if cfg else None, 0),
            banner_font_family=_eff(q_banner_font_family, cfg.banner_font_family if cfg else None, "Montserrat"),
            category_font_family=_eff(q_category_font_family, cfg.category_font_family if cfg else None, "Montserrat"),
            text_box_x_pct=_eff(q_text_box_x_pct, cfg.text_box_x_pct if cfg else None, 0),
            text_box_y_pct=_eff(q_text_box_y_pct, cfg.text_box_y_pct if cfg else None, 70),
            text_box_w_pct=_eff(q_text_box_w_pct, cfg.text_box_w_pct if cfg else None, 100),
            text_bg_border_radius=_eff(q_text_bg_border_radius, cfg.text_bg_border_radius if cfg else None, 0),
            text_bg_border_width=_eff(q_text_bg_border_width, cfg.text_bg_border_width if cfg else None, 0),
            text_bg_border_color=_eff(q_text_bg_border_color, cfg.text_bg_border_color if cfg else None, "#ffffff"),
            text_bg_height_pct=_eff(q_text_bg_height_pct, cfg.text_bg_height_pct if cfg else None, 0),
            banner_border_radius=_eff(q_banner_border_radius, cfg.banner_border_radius if cfg else None, None),
            banner_border_width=_eff(q_banner_border_width, cfg.banner_border_width if cfg else None, 0),
            banner_border_color=_eff(q_banner_border_color, cfg.banner_border_color if cfg else None, "#ffffff"),
            banner_full_width=bool(_eff(q_banner_full_width, (1 if cfg and cfg.banner_full_width else 0) if cfg else None, 0)),
            text_bg_fill_to_bottom=bool(_eff(q_text_bg_fill_to_bottom, (1 if cfg and cfg.text_bg_fill_to_bottom else 0) if cfg else None, 0)),
            title_shadow=bool(_eff(q_title_shadow, (1 if cfg is None or cfg.title_shadow else 0) if cfg else None, 1)),
        )
    except Exception as exc:
        log.error("Error generando imagen de preview: %s", exc, exc_info=True)
        return Response(f"Error: {type(exc).__name__}: {exc}".encode(), status_code=500, media_type="text/plain")

    return Response(content=ig_bytes, media_type="image/jpeg")


# ─── Detectar IG ID ──────────────────────────────────────────────────────────

@router.post("/{account_id}/fetch-ig-id")
async def fetch_ig_id(account_id: int, request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = _get_account(db, account_id)
    if not cfg or not cfg.encrypted_access_token:
        return JSONResponse({"ok": False, "error": "No hay token guardado"})

    token = decrypt_value(cfg.encrypted_access_token)
    try:
        r = http_requests.get(
            f"{IG_GRAPH_BASE}/me",
            params={"fields": "id,username,name", "access_token": token},
            timeout=20,
        )
        me = r.json()
        if "error" not in me and me.get("id"):
            return JSONResponse({"ok": True, "accounts": [{
                "ig_id": me["id"],
                "username": me.get("username", ""),
                "name": me.get("name", ""),
                "page_name": "Instagram Login",
            }]})
        err_msg = me.get("error", {}).get("message", "Error desconocido") if "error" in me else "No se pudo obtener el ID"
        return JSONResponse({
            "ok": False, "no_pages": True, "fb_user": "",
            "error": f"No se pudo obtener el ID: {err_msg}. Reconectá con el botón 'Conectar con Meta'.",
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]})


@router.post("/{account_id}/fetch-ig-id-by-business")
async def fetch_ig_id_by_business(account_id: int, request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    business_id = str(body.get("business_id", "")).strip()
    if not business_id:
        return JSONResponse({"ok": False, "error": "Falta el Business ID"})

    cfg = _get_account(db, account_id)
    if not cfg or not cfg.encrypted_access_token:
        return JSONResponse({"ok": False, "error": "No hay token guardado"})

    token = decrypt_value(cfg.encrypted_access_token)
    try:
        r = http_requests.get(
            f"{GRAPH_BASE}/{business_id}/instagram_accounts",
            params={"access_token": token, "fields": "id,username,name"},
            timeout=20,
        )
        data = r.json()
        if "error" in data:
            r2 = http_requests.get(
                f"{GRAPH_BASE}/{business_id}/owned_instagram_accounts",
                params={"access_token": token, "fields": "id,username,name"},
                timeout=20,
            )
            data = r2.json()
        if "error" in data:
            return JSONResponse({"ok": False, "error": data["error"].get("message", "Error de Meta")})
        accounts = [
            {"ig_id": a["id"], "username": a.get("username", ""), "name": a.get("name", ""), "page_name": "Business Portfolio"}
            for a in data.get("data", []) if a.get("id")
        ]
        if not accounts:
            return JSONResponse({"ok": False, "error": "No se encontraron cuentas de Instagram en ese Business Portfolio"})
        return JSONResponse({"ok": True, "accounts": accounts})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]})


# ─── Publicar prueba ─────────────────────────────────────────────────────────

@router.post("/{account_id}/test-publish")
async def test_publish_instagram(account_id: int, request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = _get_account(db, account_id)
    if not cfg or not cfg.ig_user_id or not cfg.encrypted_access_token:
        return JSONResponse({"ok": False, "error": "Configurá el ID de cuenta y el token antes de publicar"})
    if not cfg.is_active:
        return JSONResponse({"ok": False, "error": "Esta cuenta está inactiva — activala antes de publicar"})

    try:
        import urllib.request as _ur
        from app.models import Log, WordPressSettings
        from app.services.image_template_service import build_instagram_image
        from app.services.instagram_service import publish_image as ig_publish
        from app.services.wordpress_service import upload_media

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
            "Prueba de diseño — AutoNews",
            logo_path=cfg.logo_path,
            logo_position=cfg.logo_position or "bottom-right",
            logo_size=cfg.logo_size if cfg.logo_size is not None else 180,
            gradient_color=cfg.gradient_color or "#000000",
            gradient_opacity=cfg.gradient_opacity if cfg.gradient_opacity is not None else 200,
            gradient_height=cfg.gradient_height or 480,
            font_size=cfg.font_size or 62,
            text_color=cfg.text_color or "#ffffff",
            banner_text=cfg.banner_text or None,
            banner_color=cfg.banner_color or "#e53935",
            banner_text_color=cfg.banner_text_color or "#ffffff",
            text_align=cfg.text_align or "left",
            title_y_offset=cfg.title_y_offset if cfg.title_y_offset is not None else 0,
            font_family=cfg.font_family or "Montserrat",
            text_bg_color=cfg.text_bg_color or "#000000",
            text_bg_opacity=cfg.text_bg_opacity if cfg.text_bg_opacity is not None else 0,
            font_weight=cfg.font_weight or "bold",
            banner_style=cfg.banner_style or "pill",
            banner_font_weight=cfg.banner_font_weight or "bold",
            banner_y_offset=cfg.banner_y_offset if cfg.banner_y_offset is not None else 0,
            banner_align=cfg.banner_align or "center",
            text_bg_padding_x=cfg.text_bg_padding_x if cfg.text_bg_padding_x is not None else 0,
            text_bg_padding_y=cfg.text_bg_padding_y if cfg.text_bg_padding_y is not None else 18,
            text_bg_full_width=cfg.text_bg_full_width if cfg.text_bg_full_width is not None else True,
            title_max_lines=cfg.title_max_lines or 4,
            category="CATEGORÍA EJEMPLO" if cfg.show_category else None,
            show_category=bool(cfg.show_category),
            category_bg_color=cfg.category_bg_color or "#e53935",
            category_text_color=cfg.category_text_color or "#ffffff",
            category_x_percent=cfg.category_x_percent if cfg.category_x_percent is not None else 0,
            category_y_percent=cfg.category_y_percent if cfg.category_y_percent is not None else 0,
            banner_font_family=cfg.banner_font_family or "Montserrat",
            category_font_family=cfg.category_font_family or "Montserrat",
            text_box_x_pct=cfg.text_box_x_pct if cfg.text_box_x_pct is not None else 0,
            text_box_y_pct=cfg.text_box_y_pct if cfg.text_box_y_pct is not None else 70,
            text_box_w_pct=cfg.text_box_w_pct if cfg.text_box_w_pct is not None else 100,
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
            db.add(Log(level="INFO", message=f"[Instagram:{cfg.name}] Prueba publicada", source="instagram"))
            db.commit()
        return JSONResponse(result)

    except Exception as exc:
        log.error("Error en test_publish_instagram: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)[:300]})


# ─── Probar conexión ─────────────────────────────────────────────────────────

@router.post("/{account_id}/test")
async def test_instagram(account_id: int, request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = _get_account(db, account_id)
    if not cfg or not cfg.ig_user_id or not cfg.encrypted_access_token:
        return JSONResponse({"ok": False, "error": "Completá todos los campos antes de probar"})
    token = decrypt_value(cfg.encrypted_access_token)
    return JSONResponse(test_connection(cfg.ig_user_id, token))


# ─── Renovar token ───────────────────────────────────────────────────────────

@router.post("/{account_id}/refresh-token")
async def do_refresh_token(account_id: int, request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = _get_account(db, account_id)
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


# ─── OAuth ───────────────────────────────────────────────────────────────────

def _oauth_callback_url(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://") and not base.startswith("http://localhost"):
        base = "https://" + base[len("http://"):]
    return f"{base}/settings/instagram/oauth-callback"


@router.get("/{account_id}/oauth-start")
async def oauth_start(account_id: int, request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return RedirectResponse("/", status_code=302)
    cfg = _get_account(db, account_id)
    if not cfg or not cfg.app_id or not cfg.encrypted_app_secret:
        return RedirectResponse(
            f"/settings/instagram/{account_id}?err=Primero+guarda+el+App+ID+y+App+Secret",
            status_code=302,
        )
    callback = _oauth_callback_url(request)
    url = (
        f"{IG_OAUTH_URL}"
        f"?client_id={cfg.app_id}"
        f"&redirect_uri={urllib.parse.quote(callback, safe='')}"
        f"&scope={OAUTH_SCOPES}"
        f"&response_type=code"
        f"&state={account_id}"
    )
    return RedirectResponse(url, status_code=302)


def _popup_response(ok: bool, message: str, account_id: int | None = None) -> HTMLResponse:
    base = f"/settings/instagram/{account_id}" if account_id else "/settings/instagram"
    escaped = message.replace("'", "\\'").replace("\n", " ")
    key = "msg" if ok else "err"
    dest = f"{base}?{key}={urllib.parse.quote(message)}"
    if ok:
        body_html = f"<p style='color:green'>✓ {escaped}</p>"
    else:
        body_html = f"<p style='color:red'>✗ {escaped}</p>"
    script = (
        f"if(window.opener&&!window.opener.closed){{"
        f"window.opener.location.href='{dest}';"
        f"setTimeout(()=>window.close(),300);"
        f"}}else{{window.location.href='{dest}';}}"
    )
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
    state: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if not _require_admin(request, db):
        return RedirectResponse("/", status_code=302)

    account_id = int(state) if state and state.isdigit() else None

    if error or not code:
        return _popup_response(False, f"OAuth cancelado: {error or 'sin codigo'}", account_id)

    cfg = _get_account(db, account_id) if account_id else None
    if not cfg or not cfg.app_id or not cfg.encrypted_app_secret:
        return _popup_response(False, "Configuracion incompleta: falta App ID o App Secret", account_id)

    try:
        app_secret = decrypt_value(cfg.encrypted_app_secret)
        callback = _oauth_callback_url(request)

        r = http_requests.post(
            IG_TOKEN_URL,
            data={
                "client_id": cfg.app_id,
                "client_secret": app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": callback,
                "code": code,
            },
            timeout=30,
        )
        data = r.json()
        if "error" in data or "error_type" in data:
            err_msg = data.get("error_message") or data.get("error", {}).get("message", "Error de Meta")
            hint = " — verificá que el App Secret sea correcto" if "secret" in str(err_msg).lower() else ""
            return _popup_response(False, err_msg + hint, account_id)

        short_token = data.get("access_token", "")
        ig_user_id  = str(data.get("user_id", ""))
        if not short_token:
            return _popup_response(False, "Instagram no devolvió el token", account_id)

        r2 = http_requests.get(
            f"{IG_GRAPH_BASE}/access_token",
            params={"grant_type": "ig_exchange_token", "client_secret": app_secret, "access_token": short_token},
            timeout=30,
        )
        data2 = r2.json()
        long_token = data2.get("access_token", short_token)
        expires_in = data2.get("expires_in")

        cfg.encrypted_access_token = encrypt_value(long_token)
        if ig_user_id and not cfg.ig_user_id:
            cfg.ig_user_id = ig_user_id
        if expires_in:
            from datetime import timedelta
            cfg.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        else:
            try:
                cfg.token_expires_at = token_expires_at(cfg.app_id, app_secret, long_token)
            except Exception:
                pass
        db.commit()

        extra = f" (ID: {ig_user_id})" if ig_user_id and not cfg.ig_user_id else ""
        return _popup_response(True, f"Token obtenido y guardado correctamente{extra}", account_id)

    except Exception as exc:
        log.error("OAuth callback error: %s", exc, exc_info=True)
        return _popup_response(False, str(exc)[:200], account_id)
