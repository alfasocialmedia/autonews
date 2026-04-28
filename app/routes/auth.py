from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import authenticate_user, change_password, get_current_user
from app.database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("username"):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, username, password)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Usuario o contraseña incorrectos"},
            status_code=401,
        )
    request.session["username"] = user.username
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@router.get("/perfil", response_class=HTMLResponse)
async def profile_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})


@router.post("/perfil/password")
async def change_password_post(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    from app.auth import verify_password

    error = None
    if not verify_password(current_password, user.hashed_password):
        error = "La contraseña actual es incorrecta"
    elif new_password != confirm_password:
        error = "Las contraseñas nuevas no coinciden"
    elif len(new_password) < 8:
        error = "La contraseña debe tener al menos 8 caracteres"
    else:
        change_password(db, user, new_password)
        return templates.TemplateResponse(
            "profile.html",
            {"request": request, "user": user, "success": "Contraseña actualizada correctamente"},
        )

    return templates.TemplateResponse(
        "profile.html",
        {"request": request, "user": user, "error": error},
        status_code=400,
    )
