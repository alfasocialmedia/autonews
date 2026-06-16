from __future__ import annotations

import json
import uuid
from typing import List

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

# Cache en memoria: token → {ai_result, image_url, inline_images, embeds, item}
_preview_cache: dict[str, dict] = {}

from app.auth import get_current_user, user_has_module
from app.database import get_db
from app.models import InstagramSettings, ProcessedRssItem, RssFeed, WordPressSettings
from app.services.rss_service import fetch_rss_items, scrape_category_page, test_rss_feed, test_web_source

router = APIRouter(prefix="/settings/rss")
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def rss_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user_has_module(user, "rss"):
        return RedirectResponse("/login" if not user else "/", status_code=302)

    feeds = db.query(RssFeed).order_by(RssFeed.created_at.desc()).all()
    for feed in feeds:
        feed._published_count = db.query(ProcessedRssItem).filter(
            ProcessedRssItem.rss_feed_id == feed.id,
            ProcessedRssItem.status == "published",
        ).count()
        feed._wp_site_ids = json.loads(feed.wp_site_ids) if feed.wp_site_ids else []

    wp_sites = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).order_by(WordPressSettings.id).all()
    ig_accounts = db.query(InstagramSettings).order_by(InstagramSettings.id).all()

    return templates.TemplateResponse(
        "settings_rss.html", {"request": request, "user": user, "feeds": feeds, "wp_sites": wp_sites, "ig_accounts": ig_accounts}
    )


def _parse_keyword_filter(raw: str) -> str | None:
    """Normaliza el filtro de palabras clave: minúsculas, sin espacios extra."""
    cleaned = ",".join(k.strip().lower() for k in raw.split(",") if k.strip())
    return cleaned or None


@router.post("/add")
async def add_feed(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    feed_type: str = Form("rss"),
    check_interval_minutes: int = Form(60),
    articles_per_check: int = Form(1),
    max_articles_per_day: int = Form(5),
    keyword_filter: str = Form(""),
    wp_category_id: str = Form(""),
    wp_category_name: str = Form(""),
    wp_site_ids: List[str] = Form([]),
    instagram_settings_id: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user or not user_has_module(user, "rss"):
        return RedirectResponse("/login" if not user else "/", status_code=302)

    ids = [int(x) for x in wp_site_ids if x.isdigit()]
    feed = RssFeed(
        name=name.strip(),
        url=url.strip(),
        feed_type=feed_type if feed_type in ("rss", "web") else "rss",
        check_interval_minutes=check_interval_minutes,
        articles_per_check=max(1, articles_per_check),
        max_articles_per_day=max_articles_per_day,
        keyword_filter=_parse_keyword_filter(keyword_filter),
        wp_category_id=int(wp_category_id) if wp_category_id.strip().isdigit() else None,
        wp_category_name=wp_category_name.strip() or None,
        wp_site_ids=json.dumps(ids) if ids else None,
        instagram_settings_id=int(instagram_settings_id) if instagram_settings_id.strip().isdigit() else None,
    )
    db.add(feed)
    db.commit()
    return RedirectResponse("/settings/rss?msg=Feed+agregado+correctamente", status_code=302)


@router.post("/{feed_id}/edit")
async def edit_feed(
    feed_id: int,
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    feed_type: str = Form("rss"),
    check_interval_minutes: int = Form(60),
    articles_per_check: int = Form(1),
    max_articles_per_day: int = Form(5),
    keyword_filter: str = Form(""),
    wp_category_id: str = Form(""),
    wp_category_name: str = Form(""),
    wp_site_ids: List[str] = Form([]),
    instagram_settings_id: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user or not user_has_module(user, "rss"):
        return RedirectResponse("/login" if not user else "/", status_code=302)

    feed = db.query(RssFeed).filter(RssFeed.id == feed_id).first()
    if feed:
        ids = [int(x) for x in wp_site_ids if x.isdigit()]
        feed.name = name.strip()
        feed.url = url.strip()
        feed.feed_type = feed_type if feed_type in ("rss", "web") else "rss"
        feed.check_interval_minutes = check_interval_minutes
        feed.articles_per_check = max(1, articles_per_check)
        feed.max_articles_per_day = max_articles_per_day
        feed.keyword_filter = _parse_keyword_filter(keyword_filter)
        feed.wp_category_id = int(wp_category_id) if wp_category_id.strip().isdigit() else None
        feed.wp_category_name = wp_category_name.strip() or None
        feed.wp_site_ids = json.dumps(ids) if ids else None
        feed.instagram_settings_id = int(instagram_settings_id) if instagram_settings_id.strip().isdigit() else None
        db.commit()
        db.refresh(feed)
        saved_type = feed.feed_type or "rss"
        return RedirectResponse(f"/settings/rss?msg=Feed+actualizado+(tipo:+{saved_type})", status_code=302)
    return RedirectResponse("/settings/rss?msg=Feed+actualizado", status_code=302)


@router.post("/{feed_id}/toggle")
async def toggle_feed(feed_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    feed = db.query(RssFeed).filter(RssFeed.id == feed_id).first()
    if not feed:
        return JSONResponse({"error": "not found"}, status_code=404)

    feed.is_active = not feed.is_active
    db.commit()
    return JSONResponse({"active": feed.is_active})


@router.post("/{feed_id}/delete")
async def delete_feed(feed_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user_has_module(user, "rss"):
        return RedirectResponse("/login" if not user else "/", status_code=302)

    feed = db.query(RssFeed).filter(RssFeed.id == feed_id).first()
    if feed:
        db.delete(feed)
        db.commit()
    return RedirectResponse("/settings/rss?msg=Feed+eliminado", status_code=302)


class UrlTestRequest(BaseModel):
    url: str


class UrlTestRequestV2(BaseModel):
    url: str
    feed_type: str = "rss"


@router.post("/test-url")
async def test_url(payload: UrlTestRequestV2, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if payload.feed_type == "web":
        success, message = test_web_source(payload.url.strip())
    else:
        success, message = test_rss_feed(payload.url.strip())
    return JSONResponse({"success": success, "message": message})


@router.post("/{feed_id}/test")
async def test_feed(feed_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    feed = db.query(RssFeed).filter(RssFeed.id == feed_id).first()
    if not feed:
        return JSONResponse({"success": False, "message": "Feed no encontrado"})

    try:
        if (feed.feed_type or "rss") == "web":
            items = scrape_category_page(feed.url)
            label = f"Fuente web — {len(items)} artículos encontrados." if items else "No se encontraron artículos."
        else:
            items = fetch_rss_items(feed.url)
            label = f"Feed válido — {len(items)} artículos encontrados."
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})

    if not items:
        return JSONResponse({"success": False, "message": label if 'label' in dir() else "Sin artículos."})

    guids = [it["guid"] for it in items[:20]]
    processed = {
        row.guid: row.status
        for row in db.query(ProcessedRssItem).filter(ProcessedRssItem.guid.in_(guids)).all()
    }

    # Ordenar: primero los no procesados, luego el resto
    _status_order = {"new": 0, "skipped": 1, "error": 2, "published": 3}
    items_sorted = sorted(
        items[:20],
        key=lambda it: _status_order.get(processed.get(it["guid"], "new"), 0),
    )

    article_list = []
    for it in items_sorted[:10]:
        article_list.append({
            "guid": it["guid"],
            "title": it["title"],
            "link": it["link"],
            "published_at": it["published_at"].strftime("%d/%m/%Y %H:%M") if it["published_at"] else "",
            "status": processed.get(it["guid"], "new"),
        })

    return JSONResponse({
        "success": True,
        "message": label,
        "items": article_list,
    })


class PublishNowRequest(BaseModel):
    guid: str


@router.post("/{feed_id}/publish-now")
async def publish_now(
    feed_id: int,
    payload: PublishNowRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    feed = db.query(RssFeed).filter(RssFeed.id == feed_id).first()
    if not feed:
        return JSONResponse({"success": False, "message": "Feed no encontrado"})

    try:
        if (feed.feed_type or "rss") == "web":
            items = scrape_category_page(feed.url)
        else:
            items = fetch_rss_items(feed.url)
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"No se pudo obtener artículos: {exc}"})

    item = next((it for it in items if it["guid"] == payload.guid), None)
    if not item:
        return JSONResponse({"success": False, "message": "Artículo no encontrado en el feed actual"})

    try:
        from app.worker import publish_rss_item_now
        result = publish_rss_item_now(db, feed, item)
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})


class GeneratePreviewRequest(BaseModel):
    guid: str


class ConfirmPublishRequest(BaseModel):
    guid: str
    token: str


@router.post("/{feed_id}/generate-preview")
async def generate_preview(
    feed_id: int,
    payload: GeneratePreviewRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    feed = db.query(RssFeed).filter(RssFeed.id == feed_id).first()
    if not feed:
        return JSONResponse({"success": False, "message": "Feed no encontrado"})

    try:
        if (feed.feed_type or "rss") == "web":
            items = scrape_category_page(feed.url)
        else:
            items = fetch_rss_items(feed.url)
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"No se pudo obtener artículos: {exc}"})

    item = next((it for it in items if it["guid"] == payload.guid), None)
    if not item:
        return JSONResponse({"success": False, "message": "Artículo no encontrado en el feed"})

    try:
        from app.worker import generate_rss_preview
        preview = generate_rss_preview(db, feed, item)

        token = str(uuid.uuid4())
        _preview_cache[token] = {
            "ai_result": preview["_ai_result"],
            "image_url": preview["image_url"],
            "inline_images": preview["_inline_images"],
            "embeds": preview["_embeds"],
            "item": {
                "guid": item["guid"],
                "title": item["title"],
                "link": item["link"],
                "published_at": item.get("published_at"),
            },
        }

        return JSONResponse({
            "success": True,
            "token": token,
            "title": preview["title"],
            "content": preview["content"],
            "summary": preview["summary"],
            "category": preview["category"],
            "tags": preview.get("tags", []),
            "image_url": preview["image_url"],
        })
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})


@router.post("/{feed_id}/confirm-publish")
async def confirm_publish(
    feed_id: int,
    payload: ConfirmPublishRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    cached = _preview_cache.pop(payload.token, None)
    if not cached:
        return JSONResponse({"success": False, "message": "Vista previa expirada — generá el preview nuevamente"})

    feed = db.query(RssFeed).filter(RssFeed.id == feed_id).first()
    if not feed:
        return JSONResponse({"success": False, "message": "Feed no encontrado"})

    try:
        from app.worker import confirm_publish_rss_item
        result = confirm_publish_rss_item(db, feed, cached)
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})
