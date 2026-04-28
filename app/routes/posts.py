import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Post, ProcessedEmail

router = APIRouter(prefix="/posts")
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def posts_list(
    request: Request,
    page: int = Query(1, ge=1),
    status: str = Query(""),
    search: str = Query(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    per_page = 25
    offset = (page - 1) * per_page

    query = db.query(ProcessedEmail).order_by(desc(ProcessedEmail.created_at))
    if status:
        query = query.filter(ProcessedEmail.status == status)
    if search:
        query = query.filter(ProcessedEmail.subject.ilike(f"%{search}%"))

    total = query.count()
    emails = query.offset(offset).limit(per_page).all()
    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse(
        "posts.html",
        {
            "request": request,
            "user": user,
            "emails": emails,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "status_filter": status,
            "search": search,
        },
    )


@router.get("/{email_id}/detail", response_class=HTMLResponse)
async def email_detail(request: Request, email_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    processed = db.query(ProcessedEmail).filter(ProcessedEmail.id == email_id).first()
    if not processed:
        return RedirectResponse("/posts/", status_code=302)

    posts = db.query(Post).filter(Post.processed_email_id == email_id).all()

    # Parse AI response JSON if available
    ai_data = None
    if processed.ai_response:
        try:
            ai_data = json.loads(processed.ai_response)
        except Exception:
            ai_data = {"raw": processed.ai_response}

    return templates.TemplateResponse(
        "post_detail.html",
        {
            "request": request,
            "user": user,
            "email": processed,
            "posts": posts,
            "ai_data": ai_data,
        },
    )


@router.post("/{email_id}/retry")
async def retry_email(request: Request, email_id: int, db: Session = Depends(get_db)):
    if not get_current_user(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    processed = db.query(ProcessedEmail).filter(ProcessedEmail.id == email_id).first()
    if not processed:
        return JSONResponse({"success": False, "message": "No encontrado"})
    processed.status = "received"
    processed.error_message = None
    db.commit()
    return JSONResponse({"success": True, "message": "Marcado para reprocesar en el próximo ciclo"})


@router.post("/{email_id}/delete")
async def delete_email(request: Request, email_id: int, db: Session = Depends(get_db)):
    if not get_current_user(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    processed = db.query(ProcessedEmail).filter(ProcessedEmail.id == email_id).first()
    if processed:
        db.delete(processed)
        db.commit()
        return JSONResponse({"success": True})
    return JSONResponse({"success": False, "message": "No encontrado"})
