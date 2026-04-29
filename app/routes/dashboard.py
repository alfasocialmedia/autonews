import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Log, Post, ProcessedEmail

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # Estadísticas globales
    total_received = db.query(ProcessedEmail).count()
    total_processed = db.query(ProcessedEmail).filter(ProcessedEmail.status != "received").count()
    total_published = db.query(Post).count()
    total_errors = db.query(ProcessedEmail).filter(ProcessedEmail.status == "error").count()

    # Últimas 5 publicaciones
    recent_posts = (
        db.query(Post).order_by(Post.created_at.desc()).limit(5).all()
    )

    # Últimos 5 errores
    recent_errors = (
        db.query(Log)
        .filter(Log.level == "ERROR")
        .order_by(Log.created_at.desc())
        .limit(5)
        .all()
    )

    # Datos gráfico: publicaciones por día (últimos 7 días)
    posts_by_day = []
    for i in range(6, -1, -1):
        day = datetime.utcnow() - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day.replace(hour=23, minute=59, second=59)
        count = (
            db.query(Post)
            .filter(Post.created_at >= day_start, Post.created_at <= day_end)
            .count()
        )
        posts_by_day.append({"label": day.strftime("%d/%m"), "count": count})

    # Publicaciones por categoría
    cat_rows = (
        db.query(Post.category, func.count(Post.id).label("total"))
        .group_by(Post.category)
        .order_by(func.count(Post.id).desc())
        .limit(8)
        .all()
    )
    categories_data = [{"name": r.category or "Sin categoría", "count": r.total} for r in cat_rows]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "total_received": total_received,
            "total_processed": total_processed,
            "total_published": total_published,
            "total_errors": total_errors,
            "recent_posts": recent_posts,
            "recent_errors": recent_errors,
            "posts_by_day_json": json.dumps(posts_by_day),
            "categories_json": json.dumps(categories_data),
        },
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    logs = db.query(Log).order_by(Log.created_at.desc()).limit(200).all()

    return templates.TemplateResponse(
        "logs.html", {"request": request, "user": user, "logs": logs}
    )


@router.post("/worker/trigger")
async def trigger_worker(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != "admin":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        from app.worker import process_emails
        process_emails()
        return JSONResponse({"success": True, "message": "Ciclo ejecutado. Revisá los Logs."})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})
