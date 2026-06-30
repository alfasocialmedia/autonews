import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import ALL_MODULES, create_user, get_current_user, hash_password
from app.database import get_db
from app.models import (
    EmailAccount, InstagramSettings, RssFeed, User,
    WhatsAppSettings, WordPressSettings,
)

router = APIRouter(prefix="/usuarios")
templates = Jinja2Templates(directory="app/templates")


def _require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user:
        return None, RedirectResponse("/login", status_code=302)
    if user.role != "admin":
        return None, RedirectResponse("/", status_code=302)
    return user, None


def _parse_permissions(form_data: list[str]) -> str:
    """Convierte la lista de slugs seleccionados en JSON."""
    valid_slugs = {slug for slug, _, _ in ALL_MODULES}
    perms = [s for s in form_data if s in valid_slugs]
    return json.dumps(perms)


@router.get("/", response_class=HTMLResponse)
async def users_list(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_admin(request, db)
    if redirect:
        return redirect
    users = db.query(User).order_by(User.id).all()
    # Adjuntar la lista de perms ya parseada para el template
    for u in users:
        u._perms = json.loads(u.permissions or "[]") if u.role != "admin" else []
    return templates.TemplateResponse(
        "users.html", {
            "request": request,
            "user": user,
            "users": users,
            "all_modules": ALL_MODULES,
        }
    )


@router.post("/crear")
async def user_create(
    request: Request,
    username: str = Form(...),
    email: str = Form(""),
    password: str = Form(...),
    role: str = Form("editor"),
    permissions: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    user, redirect = _require_admin(request, db)
    if redirect:
        return redirect

    if role not in ("admin", "editor"):
        role = "editor"

    exists = db.query(User).filter(User.username == username).first()
    users = db.query(User).order_by(User.id).all()
    for u in users:
        u._perms = json.loads(u.permissions or "[]") if u.role != "admin" else []
    if exists:
        return templates.TemplateResponse(
            "users.html",
            {"request": request, "user": user, "users": users,
             "all_modules": ALL_MODULES,
             "error": f"El usuario '{username}' ya existe."},
            status_code=400,
        )
    new_user = create_user(db, username, password, email or None)
    new_user.role = role
    if role == "editor":
        new_user.permissions = _parse_permissions(permissions)
    db.commit()
    return RedirectResponse("/usuarios/?ok=creado", status_code=303)


@router.post("/{user_id}/editar")
async def user_edit(
    user_id: int,
    request: Request,
    username: str = Form(...),
    email: str = Form(""),
    password: str = Form(""),
    role: str = Form("editor"),
    permissions: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    current, redirect = _require_admin(request, db)
    if redirect:
        return redirect

    if role not in ("admin", "editor"):
        role = "editor"

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return RedirectResponse("/usuarios/?err=no+encontrado", status_code=303)

    duplicate = db.query(User).filter(User.username == username, User.id != user_id).first()
    if duplicate:
        users = db.query(User).order_by(User.id).all()
        for u in users:
            u._perms = json.loads(u.permissions or "[]") if u.role != "admin" else []
        return templates.TemplateResponse(
            "users.html",
            {"request": request, "user": current, "users": users,
             "all_modules": ALL_MODULES,
             "error": f"El nombre '{username}' ya está en uso."},
            status_code=400,
        )

    target.username = username
    target.email = email or None
    target.role = role
    if role == "editor":
        target.permissions = _parse_permissions(permissions)
    else:
        target.permissions = None  # admin no necesita permisos explícitos
    if password:
        target.hashed_password = hash_password(password)
    db.commit()

    if current.id == user_id:
        request.session["username"] = username

    return RedirectResponse("/usuarios/?ok=editado", status_code=303)


_RESOURCE_MODELS = {
    "whatsapp": WhatsAppSettings,
    "rss": RssFeed,
    "wordpress": WordPressSettings,
    "instagram": InstagramSettings,
    "email": EmailAccount,
}


@router.get("/{user_id}/recursos")
async def user_recursos(user_id: int, request: Request, db: Session = Depends(get_db)):
    current, redirect = _require_admin(request, db)
    if redirect:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)

    def _fmt(items):
        return [
            {"id": r.id, "name": r.name or str(r.id), "assigned": r.owner_user_id == user_id}
            for r in items
        ]

    return JSONResponse({
        "user_id": user_id,
        "username": target.username,
        "whatsapp":  _fmt(db.query(WhatsAppSettings).order_by(WhatsAppSettings.id).all()),
        "rss":       _fmt(db.query(RssFeed).order_by(RssFeed.id).all()),
        "wordpress": _fmt(db.query(WordPressSettings).order_by(WordPressSettings.id).all()),
        "instagram": _fmt(db.query(InstagramSettings).order_by(InstagramSettings.id).all()),
        "email":     _fmt(db.query(EmailAccount).order_by(EmailAccount.id).all()),
    })


@router.post("/{user_id}/asignar-recursos")
async def asignar_recursos(user_id: int, request: Request, db: Session = Depends(get_db)):
    current, redirect = _require_admin(request, db)
    if redirect:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)

    admin = db.query(User).filter(User.role == "admin").order_by(User.id).first()
    admin_id = admin.id if admin else current.id

    body = await request.json()

    for key, model in _RESOURCE_MODELS.items():
        selected_ids = set(body.get(key, []))
        # Devolver al admin los que estaban asignados a este usuario y ya no están seleccionados
        for r in db.query(model).filter(model.owner_user_id == user_id).all():
            if r.id not in selected_ids:
                r.owner_user_id = admin_id
        # Asignar los seleccionados a este usuario
        if selected_ids:
            for r in db.query(model).filter(model.id.in_(selected_ids)).all():
                r.owner_user_id = user_id

    db.commit()
    return JSONResponse({"ok": True})


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
        for u in users:
            u._perms = json.loads(u.permissions or "[]") if u.role != "admin" else []
        return templates.TemplateResponse(
            "users.html",
            {"request": request, "user": current, "users": users,
             "all_modules": ALL_MODULES,
             "error": "No podés eliminar tu propio usuario."},
            status_code=400,
        )

    target = db.query(User).filter(User.id == user_id).first()
    if target:
        db.delete(target)
        db.commit()

    return RedirectResponse("/usuarios/?ok=eliminado", status_code=303)
