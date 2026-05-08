from __future__ import annotations

import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.database import get_db
from app.models import Post, ProcessedRssItem, WordPressSettings

router = APIRouter(prefix="/publicaciones")
templates = Jinja2Templates(directory="app/templates")

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return _TAG_RE.sub(" ", html or "").strip()


def _normalize_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


@router.get("/", response_class=HTMLResponse)
async def publicaciones_list(
    request: Request,
    page: int = Query(1, ge=1),
    fuente: str = Query(""),
    categoria: str = Query(""),
    search: str = Query(""),
    site_id: int = Query(0),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    per_page = 20
    offset = (page - 1) * per_page

    q = db.query(Post).options(joinedload(Post.wordpress_settings)).order_by(desc(Post.created_at))

    if fuente == "rss":
        q = q.filter(Post.processed_email_id.is_(None))
    elif fuente == "email":
        q = q.filter(Post.processed_email_id.isnot(None))

    if categoria:
        q = q.filter(Post.category.ilike(f"%{categoria}%"))

    if search:
        q = q.filter(Post.title.ilike(f"%{search}%"))

    if site_id:
        q = q.filter(Post.wordpress_settings_id == site_id)

    total = q.count()
    posts = q.offset(offset).limit(per_page).all()
    total_pages = max(1, (total + per_page - 1) // per_page)

    categorias = [
        r[0] for r in db.query(Post.category).filter(Post.category.isnot(None)).distinct().all()
        if r[0]
    ]

    # Todos los sitios WP (activos e inactivos) para el mapa de dominio → nombre
    all_wp_sites = db.query(WordPressSettings).all()
    domain_to_name: dict[str, str] = {}
    domain_to_id: dict[str, int] = {}
    for s in all_wp_sites:
        d = _normalize_domain(s.site_url)
        if d:
            domain_to_name[d] = s.name
            domain_to_id[d] = s.id

    # Solo los activos para el filtro del desplegable
    wp_sites = [s for s in all_wp_sites if s.is_active]
    wp_sites.sort(key=lambda s: s.name or "")

    for p in posts:
        p._preview = _strip_html(p.content)[:220] if p.content else ""
        if p.wordpress_settings:
            p._site_name = p.wordpress_settings.name
        elif p.wp_link:
            d = _normalize_domain(p.wp_link)
            p._site_name = domain_to_name.get(d, "")
        else:
            p._site_name = ""

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
            "wp_sites": wp_sites,
            "site_filter": site_id,
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
