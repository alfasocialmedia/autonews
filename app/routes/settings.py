from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.crypto import decrypt_value, encrypt_value, mask_value
from app.database import get_db
from app.models import CategoryMapping, ElevenLabsSettings, EmailAccount, GoogleDriveSettings, GroqSettings, WordPressSettings
from app.services.email_service import test_imap_connection
from app.services.groq_service import PROVIDERS, test_groq_connection
from app.services.wordpress_service import get_categories, test_wordpress_connection

router = APIRouter(prefix="/settings")
templates = Jinja2Templates(directory="app/templates")

DEFAULT_PROMPT = """Eres un redactor periodístico profesional. Tu tarea es transformar el contenido \
de un correo electrónico en un artículo de noticias completo, detallado y bien estructurado en español.

El artículo debe:
1. Tener un título atractivo, claro y descriptivo (sin prefijos como Fwd, Re, FW)
2. Comenzar con un párrafo de entradilla que resuma lo más importante
3. Desarrollar la noticia en profundidad con subtítulos <h2> para cada sección
4. Incluir contexto, antecedentes y posibles consecuencias del hecho
5. Citar textualmente frases relevantes usando <blockquote> si las hay
6. Usar HTML: <p>, <h2>, <h3>, <strong>, <em>, <ul>, <li>, <blockquote>
7. Ser objetivo, profesional y factual — no inventar datos
8. Tener entre 600 y 1200 palabras

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
            "providers": PROVIDERS,
        },
    )


@router.post("/groq/save")
async def save_groq(
    request: Request,
    api_key: str = Form(""),
    model: str = Form("llama-3.3-70b-versatile"),
    base_prompt: str = Form(...),
    provider: str = Form("groq"),
    api_base_url: str = Form(""),
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
        groq.provider = provider
        groq.api_base_url = api_base_url.strip() or None
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
            provider=provider,
            api_base_url=api_base_url.strip() or None,
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
        ok, msg = test_groq_connection(
            key, groq.model,
            provider=groq.provider or "groq",
            api_base_url=groq.api_base_url,
        )
        return JSONResponse({"success": ok, "message": msg})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})


# ──────────────────────────────────────────────────────────────────────────────
#  GOOGLE DRIVE
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/googledrive", response_class=HTMLResponse)
async def googledrive_settings(request: Request, db: Session = Depends(get_db)):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    cfg = db.query(GoogleDriveSettings).first()
    return templates.TemplateResponse(
        "settings_googledrive.html",
        {"request": request, "user": user, "cfg": cfg, "mask": mask_value},
    )


@router.post("/googledrive/save")
async def save_googledrive(
    request: Request,
    api_key: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    cfg = db.query(GoogleDriveSettings).first()
    if cfg:
        if api_key.strip():
            cfg.encrypted_api_key = encrypt_value(api_key)
    else:
        if not api_key.strip():
            return RedirectResponse(
                "/settings/googledrive?err=La+API+Key+es+obligatoria+la+primera+vez",
                status_code=302,
            )
        cfg = GoogleDriveSettings(encrypted_api_key=encrypt_value(api_key))
        db.add(cfg)
    db.commit()
    return RedirectResponse("/settings/googledrive?msg=API+Key+guardada+correctamente", status_code=302)


@router.post("/googledrive/test")
async def test_googledrive(request: Request, db: Session = Depends(get_db)):
    if not _require_auth(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = db.query(GoogleDriveSettings).first()
    if not cfg:
        return JSONResponse({"success": False, "message": "No hay API Key configurada"})
    try:
        import httpx
        key = decrypt_value(cfg.encrypted_api_key)
        resp = httpx.get(
            "https://www.googleapis.com/drive/v3/files",
            params={"key": key, "pageSize": 1, "fields": "files(id)"},
            timeout=10,
        )
        if resp.status_code == 200:
            return JSONResponse({"success": True, "message": "Conexión con Google Drive API exitosa"})
        error = resp.json().get("error", {}).get("message", f"HTTP {resp.status_code}")
        return JSONResponse({"success": False, "message": error})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})


# ──────────────────────────────────────────────────────────────────────────────
#  ELEVENLABS TTS
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/elevenlabs", response_class=HTMLResponse)
async def elevenlabs_settings(request: Request, db: Session = Depends(get_db)):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    cfg = db.query(ElevenLabsSettings).first()
    return templates.TemplateResponse(
        "settings_elevenlabs.html",
        {"request": request, "user": user, "cfg": cfg, "mask": mask_value},
    )


@router.post("/elevenlabs/save")
async def save_elevenlabs(
    request: Request,
    api_key: str = Form(""),
    voice_id: str = Form("pNInz6obpgDQGcFmaJgB"),
    model_id: str = Form("eleven_multilingual_v2"),
    enabled: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_auth(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    is_enabled = enabled.lower() in ("on", "true", "1", "yes")
    cfg = db.query(ElevenLabsSettings).first()
    if cfg:
        if api_key.strip():
            cfg.encrypted_api_key = encrypt_value(api_key)
        cfg.voice_id = voice_id.strip() or cfg.voice_id
        cfg.model_id = model_id.strip() or cfg.model_id
        cfg.enabled = is_enabled
    else:
        if not api_key.strip():
            return RedirectResponse(
                "/settings/elevenlabs?err=La+API+Key+es+obligatoria+la+primera+vez",
                status_code=302,
            )
        cfg = ElevenLabsSettings(
            encrypted_api_key=encrypt_value(api_key),
            voice_id=voice_id.strip() or "pNInz6obpgDQGcFmaJgB",
            model_id=model_id.strip() or "eleven_multilingual_v2",
            enabled=is_enabled,
        )
        db.add(cfg)
    db.commit()
    return RedirectResponse("/settings/elevenlabs?msg=Configuración+guardada", status_code=302)


@router.post("/elevenlabs/test")
async def test_elevenlabs(request: Request, db: Session = Depends(get_db)):
    if not _require_auth(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = db.query(ElevenLabsSettings).first()
    if not cfg:
        return JSONResponse({"success": False, "message": "No hay configuración de ElevenLabs"})
    try:
        from app.services.elevenlabs_service import test_connection
        key = decrypt_value(cfg.encrypted_api_key)
        ok, msg = test_connection(key)
        return JSONResponse({"success": ok, "message": msg})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})


@router.post("/elevenlabs/voices")
async def list_elevenlabs_voices(request: Request, db: Session = Depends(get_db)):
    if not _require_auth(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = db.query(ElevenLabsSettings).first()
    if not cfg:
        return JSONResponse({"success": False, "voices": [], "message": "Sin configuración"})
    try:
        from app.services.elevenlabs_service import list_voices
        key = decrypt_value(cfg.encrypted_api_key)
        voices = list_voices(key)
        return JSONResponse({"success": True, "voices": voices, "current_voice_id": cfg.voice_id})
    except Exception as exc:
        return JSONResponse({"success": False, "voices": [], "message": str(exc)})


@router.post("/elevenlabs/test-voice")
async def test_voice_audio(request: Request, db: Session = Depends(get_db)):
    if not _require_auth(request, db):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    cfg = db.query(ElevenLabsSettings).first()
    if not cfg:
        return JSONResponse({"success": False, "message": "Sin configuración de ElevenLabs"})
    try:
        import base64
        from app.services.elevenlabs_service import generate_audio
        key = decrypt_value(cfg.encrypted_api_key)
        audio = generate_audio(
            "Hola, esta es una prueba de voz con ElevenLabs. Si escuchás esto, la voz está funcionando correctamente.",
            key,
            cfg.voice_id,
            cfg.model_id,
        )
        audio_b64 = base64.b64encode(audio).decode()
        return JSONResponse({"success": True, "audio_b64": audio_b64, "voice_id": cfg.voice_id})
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)})
