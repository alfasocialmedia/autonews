from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.crypto import decrypt_value, encrypt_value, mask_value
from app.database import get_db
from app.models import CategoryMapping, EmailAccount, GroqSettings, WordPressSettings
from app.services.email_service import test_imap_connection
from app.services.groq_service import test_groq_connection
from app.services.wordpress_service import get_categories, test_wordpress_connection

router = APIRouter(prefix="/settings")
templates = Jinja2Templates(directory="app/templates")

DEFAULT_PROMPT = """Eres un redactor periodístico profesional. Tu tarea es transformar el contenido \
de un correo electrónico en un artículo de noticias completo y bien estructurado en español.

El artículo debe:
1. Tener un título atractivo, claro y descriptivo
2. Estar bien estructurado con párrafos claros y ordenados
3. Ser objetivo, profesional y factual
4. Usar HTML básico: <p>, <h2>, <strong>, <ul>, <li> según corresponda
5. Incluir la información más relevante del correo sin inventar datos
6. Tener entre 300 y 600 palabras

Categorías disponibles: Política, Economía, Tecnología, Deportes, Cultura, Sociedad, Internacional, General"""


def _require_auth(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user:
        return None
    return user


# ──────────────────────────────────────────────────────────────────────────────
#  CORREO / IMAP
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/email", response_class=HTMLResponse)
async def email_settings(request: Request, db: Session = Depends(get_db)):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    accounts = db.query(EmailAccount).all()
    return templates.TemplateResponse(
        "settings_email.html",
        {"request": request, "user": user, "accounts": accounts, "mask": mask_value},
    )


@router.post("/email/add")
async def add_email(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    imap_server: str = Form(...),
    imap_port: int = Form(993),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    acc = EmailAccount(
        name=name,
        email=email,
        imap_server=imap_server,
        imap_port=imap_port,
        username=username,
        encrypted_password=encrypt_value(password),
    )
    db.add(acc)
    db.commit()
    return RedirectResponse("/settings/email?msg=Cuenta+añadida+correctamente", status_code=302)


@router.post("/email/{acc_id}/edit")
async def edit_email(
    request: Request,
    acc_id: int,
    name: str = Form(...),
    email: str = Form(...),
    imap_server: str = Form(...),
    imap_port: int = Form(993),
    username: str = Form(...),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    acc = db.query(EmailAccount).filter(EmailAccount.id == acc_id).first()
    if not acc:
        return RedirectResponse("/settings/email?err=Cuenta+no+encontrada", status_code=302)

    acc.name = name
    acc.email = email
    acc.imap_server = imap_server
    acc.imap_port = imap_port
    acc.username = username
    if password.strip():
        acc.encrypted_password = encrypt_value(password)
    db.commit()
    return RedirectResponse("/settings/email?msg=Cuenta+actualizada", status_code=302)


@router.post("/email/{acc_id}/toggle")
async def toggle_email(request: Request, acc_id: int, db: Session = Depends(get_db)):
    if not _require_auth(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    acc = db.query(EmailAccount).filter(EmailAccount.id == acc_id).first()
    if not acc:
        return JSONResponse({"error": "not found"}, status_code=404)
    acc.is_active = not acc.is_active
    db.commit()
    return JSONResponse({"active": acc.is_active})


@router.post("/email/{acc_id}/test")
async def test_email(request: Request, acc_id: int, db: Session = Depends(get_db)):
    if not _require_auth(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    acc = db.query(EmailAccount).filter(EmailAccount.id == acc_id).first()
    if not acc:
        return JSONResponse({"success": False, "message": "Cuenta no encontrada"})
    try:
        pwd = decrypt_value(acc.encrypted_password)
        ok, msg = test_imap_connection(acc.imap_server, acc.imap_port, acc.username, pwd)
        return JSONResponse({"success": ok, "message": msg})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})


@router.post("/email/{acc_id}/delete")
async def delete_email(request: Request, acc_id: int, db: Session = Depends(get_db)):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    acc = db.query(EmailAccount).filter(EmailAccount.id == acc_id).first()
    if acc:
        db.delete(acc)
        db.commit()
    return RedirectResponse("/settings/email?msg=Cuenta+eliminada", status_code=302)


# ──────────────────────────────────────────────────────────────────────────────
#  WORDPRESS
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/wordpress", response_class=HTMLResponse)
async def wordpress_settings(request: Request, db: Session = Depends(get_db)):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    sites = db.query(WordPressSettings).all()
    return templates.TemplateResponse(
        "settings_wordpress.html",
        {"request": request, "user": user, "sites": sites, "mask": mask_value},
    )


@router.post("/wordpress/add")
async def add_wordpress(
    request: Request,
    name: str = Form("Principal"),
    site_url: str = Form(...),
    api_user: str = Form(...),
    app_password: str = Form(...),
    default_status: str = Form("draft"),
    db: Session = Depends(get_db),
):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    wp = WordPressSettings(
        name=name,
        site_url=site_url.rstrip("/"),
        api_user=api_user,
        encrypted_app_password=encrypt_value(app_password),
        default_status=default_status,
    )
    db.add(wp)
    db.commit()
    return RedirectResponse("/settings/wordpress?msg=Sitio+añadido+correctamente", status_code=302)


@router.post("/wordpress/{wp_id}/edit")
async def edit_wordpress(
    request: Request,
    wp_id: int,
    name: str = Form(...),
    site_url: str = Form(...),
    api_user: str = Form(...),
    app_password: str = Form(""),
    default_status: str = Form("draft"),
    db: Session = Depends(get_db),
):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    wp = db.query(WordPressSettings).filter(WordPressSettings.id == wp_id).first()
    if not wp:
        return RedirectResponse("/settings/wordpress?err=Sitio+no+encontrado", status_code=302)
    wp.name = name
    wp.site_url = site_url.rstrip("/")
    wp.api_user = api_user
    if app_password.strip():
        wp.encrypted_app_password = encrypt_value(app_password)
    wp.default_status = default_status
    db.commit()
    return RedirectResponse("/settings/wordpress?msg=Sitio+actualizado", status_code=302)


@router.post("/wordpress/{wp_id}/delete")
async def delete_wordpress(request: Request, wp_id: int, db: Session = Depends(get_db)):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    wp = db.query(WordPressSettings).filter(WordPressSettings.id == wp_id).first()
    if wp:
        db.delete(wp)
        db.commit()
    return RedirectResponse("/settings/wordpress?msg=Sitio+eliminado", status_code=302)


@router.post("/wordpress/{wp_id}/toggle")
async def toggle_wordpress(request: Request, wp_id: int, db: Session = Depends(get_db)):
    if not _require_auth(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    wp = db.query(WordPressSettings).filter(WordPressSettings.id == wp_id).first()
    if not wp:
        return JSONResponse({"error": "not found"}, status_code=404)
    wp.is_active = not wp.is_active
    db.commit()
    return JSONResponse({"active": wp.is_active})


@router.post("/wordpress/{wp_id}/test")
async def test_wp(request: Request, wp_id: int, db: Session = Depends(get_db)):
    if not _require_auth(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    wp = db.query(WordPressSettings).filter(WordPressSettings.id == wp_id).first()
    if not wp:
        return JSONResponse({"success": False, "message": "Sitio no encontrado"})
    try:
        pwd = decrypt_value(wp.encrypted_app_password)
        ok, msg = test_wordpress_connection(wp.site_url, wp.api_user, pwd)
        return JSONResponse({"success": ok, "message": msg})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})


@router.post("/wordpress/{wp_id}/categories/fetch")
async def fetch_wp_categories(request: Request, wp_id: int, db: Session = Depends(get_db)):
    if not _require_auth(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    wp = db.query(WordPressSettings).filter(WordPressSettings.id == wp_id).first()
    if not wp:
        return JSONResponse({"categories": []})
    try:
        pwd = decrypt_value(wp.encrypted_app_password)
        cats = get_categories(wp.site_url, wp.api_user, pwd)
        return JSONResponse({"categories": [{"id": c["id"], "name": c["name"]} for c in cats]})
    except Exception as exc:
        return JSONResponse({"categories": [], "error": str(exc)})


@router.post("/wordpress/{wp_id}/categories/add")
async def add_category_mapping(
    request: Request,
    wp_id: int,
    keyword: str = Form(...),
    category_id: int = Form(...),
    category_name: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    wp = db.query(WordPressSettings).filter(WordPressSettings.id == wp_id).first()
    if not wp:
        return RedirectResponse("/settings/wordpress?err=Sitio+no+encontrado", status_code=302)
    mapping = CategoryMapping(
        wordpress_settings_id=wp_id,
        keyword=keyword,
        category_id=category_id,
        category_name=category_name,
    )
    db.add(mapping)
    db.commit()
    return RedirectResponse("/settings/wordpress?msg=Mapeo+añadido", status_code=302)


@router.post("/wordpress/categories/{mid}/delete")
async def delete_category_mapping(request: Request, mid: int, db: Session = Depends(get_db)):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    m = db.query(CategoryMapping).filter(CategoryMapping.id == mid).first()
    if m:
        db.delete(m)
        db.commit()
    return RedirectResponse("/settings/wordpress?msg=Mapeo+eliminado", status_code=302)


# ──────────────────────────────────────────────────────────────────────────────
#  GROQ
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/groq", response_class=HTMLResponse)
async def groq_settings(request: Request, db: Session = Depends(get_db)):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    groq = db.query(GroqSettings).first()
    return templates.TemplateResponse(
        "settings_groq.html",
        {
            "request": request,
            "user": user,
            "groq": groq,
            "default_prompt": DEFAULT_PROMPT,
            "mask": mask_value,
        },
    )


@router.post("/groq/save")
async def save_groq(
    request: Request,
    api_key: str = Form(""),
    model: str = Form("llama-3.3-70b-versatile"),
    base_prompt: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    groq = db.query(GroqSettings).first()
    if groq:
        if api_key.strip():
            groq.encrypted_api_key = encrypt_value(api_key)
        groq.model = model
        groq.base_prompt = base_prompt
    else:
        if not api_key.strip():
            return RedirectResponse(
                "/settings/groq?err=La+API+Key+es+obligatoria+la+primera+vez",
                status_code=302,
            )
        groq = GroqSettings(
            encrypted_api_key=encrypt_value(api_key),
            model=model,
            base_prompt=base_prompt,
        )
        db.add(groq)
    db.commit()
    return RedirectResponse("/settings/groq?msg=Configuración+guardada", status_code=302)


@router.post("/groq/test")
async def test_groq_route(request: Request, db: Session = Depends(get_db)):
    if not _require_auth(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    groq = db.query(GroqSettings).first()
    if not groq:
        return JSONResponse({"success": False, "message": "No hay configuración de Groq"})
    try:
        key = decrypt_value(groq.encrypted_api_key)
        ok, msg = test_groq_connection(key, groq.model)
        return JSONResponse({"success": ok, "message": msg})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})
