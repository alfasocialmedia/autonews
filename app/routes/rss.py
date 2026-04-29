from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import ProcessedRssItem, RssFeed
from app.services.rss_service import test_rss_feed

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


@router.post("/add")
async def add_feed(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    check_interval_minutes: int = Form(60),
    max_articles_per_day: int = Form(5),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return RedirectResponse("/", status_code=302)

    feed = RssFeed(
        name=name.strip(),
        url=url.strip(),
        check_interval_minutes=check_interval_minutes,
        max_articles_per_day=max_articles_per_day,
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
    max_articles_per_day: int = Form(5),
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
        feed.max_articles_per_day = max_articles_per_day
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


@router.post("/{feed_id}/test")
async def test_feed(feed_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    feed = db.query(RssFeed).filter(RssFeed.id == feed_id).first()
    if not feed:
        return JSONResponse({"success": False, "message": "Feed no encontrado"})

    success, message = test_rss_feed(feed.url)
    return JSONResponse({"success": success, "message": message})
