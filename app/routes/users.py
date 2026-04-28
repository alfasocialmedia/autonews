from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import create_user, get_current_user, hash_password
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/usuarios")
templates = Jinja2Templates(directory="app/templates")


def _require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user:
        return None, RedirectResponse("/login", status_code=302)
    return user, None


@router.get("/", response_class=HTMLResponse)
async def users_list(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_admin(request, db)
    if redirect:
        return redirect
    users = db.query(User).order_by(User.id).all()
    return templates.TemplateResponse(
        "users.html", {"request": request, "user": user, "users": users}
    )


@router.post("/crear")
async def user_create(
    request: Request,
    username: str = Form(...),
    email: str = Form(""),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user, redirect = _require_admin(request, db)
    if redirect:
        return redirect

    exists = db.query(User).filter(User.username == username).first()
    users = db.query(User).order_by(User.id).all()
    if exists:
        return templates.TemplateResponse(
            "users.html",
            {"request": request, "user": user, "users": users,
             "error": f"El usuario '{username}' ya existe."},
            status_code=400,
        )
    create_user(db, username, password, email or None)
    return RedirectResponse("/usuarios/?ok=creado", status_code=303)


@router.post("/{user_id}/editar")
async def user_edit(
    user_id: int,
    request: Request,
    username: str = Form(...),
    email: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    current, redirect = _require_admin(request, db)
    if redirect:
        return redirect

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return RedirectResponse("/usuarios/?err=no+encontrado", status_code=303)

    duplicate = db.query(User).filter(User.username == username, User.id != user_id).first()
    if duplicate:
        users = db.query(User).order_by(User.id).all()
        return templates.TemplateResponse(
            "users.html",
            {"request": request, "user": current, "users": users,
             "error": f"El nombre '{username}' ya está en uso."},
            status_code=400,
        )

    target.username = username
    target.email = email or None
    if password:
        target.hashed_password = hash_password(password)
    db.commit()

    if current.id == user_id:
        request.session["username"] = username

    return RedirectResponse("/usuarios/?ok=editado", status_code=303)


@router.post("/{user_id}/eliminar")
async def user_delete(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    current, redirect = _require_admin(request, db)
    if redirect:
        return redirect

    if current.id == user_id:
        users = db.query(User).order_by(User.id).all()
        return templates.TemplateResponse(
            "users.html",
            {"request": request, "user": current, "users": users,
             "error": "No podés eliminar tu propio usuario."},
            status_code=400,
        )

    target = db.query(User).filter(User.id == user_id).first()
    if target:
        db.delete(target)
        db.commit()

    return RedirectResponse("/usuarios/?ok=eliminado", status_code=303)
