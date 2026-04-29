from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Post, ProcessedRssItem

router = APIRouter(prefix="/publicaciones")
templates = Jinja2Templates(directory="app/templates")

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return _TAG_RE.sub(" ", html or "").strip()


@router.get("/", response_class=HTMLResponse)
async def publicaciones_list(
    request: Request,
    page: int = Query(1, ge=1),
    fuente: str = Query(""),   # "rss" | "email" | ""
    categoria: str = Query(""),
    search: str = Query(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    per_page = 20
    offset = (page - 1) * per_page

    q = db.query(Post).order_by(desc(Post.created_at))

    if fuente == "rss":
        q = q.filter(Post.processed_email_id.is_(None))
    elif fuente == "email":
        q = q.filter(Post.processed_email_id.isnot(None))

    if categoria:
        q = q.filter(Post.category.ilike(f"%{categoria}%"))

    if search:
        q = q.filter(Post.title.ilike(f"%{search}%"))

    total = q.count()
    posts = q.offset(offset).limit(per_page).all()
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Categorías únicas para el filtro
    categorias = [
        r[0] for r in db.query(Post.category).filter(Post.category.isnot(None)).distinct().all()
        if r[0]
    ]

    # Previews de texto plano
    for p in posts:
        p._preview = _strip_html(p.content)[:220] if p.content else ""

    return templates.TemplateResponse(
        "publicaciones.html",
        {
            "request": request,
            "user": user,
            "posts": posts,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "fuente_filter": fuente,
            "categoria_filter": categoria,
            "search": search,
            "categorias": categorias,
        },
    )


@router.get("/{post_id}/preview")
async def post_preview(post_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "title": post.title or "",
        "content": post.content or "",
        "category": post.category or "",
        "wp_link": post.wp_link or "",
        "status": post.status or "",
        "created_at": post.created_at.strftime("%d/%m/%Y %H:%M") if post.created_at else "",
    })


@router.post("/{post_id}/delete")
async def delete_post(post_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    post = db.query(Post).filter(Post.id == post_id).first()
    if post:
        db.delete(post)
        db.commit()
        return JSONResponse({"success": True})
    return JSONResponse({"success": False, "message": "No encontrado"})
