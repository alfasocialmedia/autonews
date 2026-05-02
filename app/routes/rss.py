from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import ProcessedRssItem, RssFeed
from app.services.rss_service import fetch_rss_items, test_rss_feed

router = APIRouter(prefix="/settings/rss")
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def rss_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return RedirectResponse("/", status_code=302)

    feeds = db.query(RssFeed).order_by(RssFeed.created_at.desc()).all()
    # Agregar conteo de publicaciones por feed
    for feed in feeds:
        feed._published_count = db.query(ProcessedRssItem).filter(
            ProcessedRssItem.rss_feed_id == feed.id,
            ProcessedRssItem.status == "published",
        ).count()

    return templates.TemplateResponse(
        "settings_rss.html", {"request": request, "user": user, "feeds": feeds}
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
    check_interval_minutes: int = Form(60),
    articles_per_check: int = Form(1),
    max_articles_per_day: int = Form(5),
    keyword_filter: str = Form(""),
    wp_category_id: str = Form(""),
    wp_category_name: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return RedirectResponse("/", status_code=302)

    feed = RssFeed(
        name=name.strip(),
        url=url.strip(),
        check_interval_minutes=check_interval_minutes,
        articles_per_check=max(1, articles_per_check),
        max_articles_per_day=max_articles_per_day,
        keyword_filter=_parse_keyword_filter(keyword_filter),
        wp_category_id=int(wp_category_id) if wp_category_id.strip().isdigit() else None,
        wp_category_name=wp_category_name.strip() or None,
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
    check_interval_minutes: int = Form(60),
    articles_per_check: int = Form(1),
    max_articles_per_day: int = Form(5),
    keyword_filter: str = Form(""),
    wp_category_id: str = Form(""),
    wp_category_name: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return RedirectResponse("/", status_code=302)

    feed = db.query(RssFeed).filter(RssFeed.id == feed_id).first()
    if feed:
        feed.name = name.strip()
        feed.url = url.strip()
        feed.check_interval_minutes = check_interval_minutes
        feed.articles_per_check = max(1, articles_per_check)
        feed.max_articles_per_day = max_articles_per_day
        feed.keyword_filter = _parse_keyword_filter(keyword_filter)
        feed.wp_category_id = int(wp_category_id) if wp_category_id.strip().isdigit() else None
        feed.wp_category_name = wp_category_name.strip() or None
        db.commit()
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
    if not user or user.role != "admin":
        return RedirectResponse("/", status_code=302)

    feed = db.query(RssFeed).filter(RssFeed.id == feed_id).first()
    if feed:
        db.delete(feed)
        db.commit()
    return RedirectResponse("/settings/rss?msg=Feed+eliminado", status_code=302)


class UrlTestRequest(BaseModel):
    url: str


@router.post("/test-url")
async def test_url(payload: UrlTestRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
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
        items = fetch_rss_items(feed.url)
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})

    if not items:
        return JSONResponse({"success": False, "message": "El feed no contiene ítems o no es accesible."})

    guids = [it["guid"] for it in items[:10]]
    processed = {
        row.guid: row.status
        for row in db.query(ProcessedRssItem).filter(ProcessedRssItem.guid.in_(guids)).all()
    }

    article_list = []
    for it in items[:5]:
        article_list.append({
            "guid": it["guid"],
            "title": it["title"],
            "link": it["link"],
            "published_at": it["published_at"].strftime("%d/%m/%Y %H:%M") if it["published_at"] else "",
            "status": processed.get(it["guid"], "new"),
        })

    return JSONResponse({
        "success": True,
        "message": f"Feed válido — {len(items)} artículos encontrados.",
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
        items = fetch_rss_items(feed.url)
    except Exception as exc:
        return JSONResponse({"success": False, "message": f"No se pudo descargar el feed: {exc}"})

    item = next((it for it in items if it["guid"] == payload.guid), None)
    if not item:
        return JSONResponse({"success": False, "message": "Artículo no encontrado en el feed actual"})

    try:
        from app.worker import publish_rss_item_now
        result = publish_rss_item_now(db, feed, item)
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})
