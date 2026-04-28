from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.database import SessionLocal, engine
from app.models import Base, User
from app.routes import auth, dashboard, posts, settings, users


def _create_default_admin():
    db = SessionLocal()
    try:
        from app.auth import create_user
        from app.models import User

        if db.query(User).count() == 0:
            admin_pwd = os.getenv("ADMIN_PASSWORD", "admin123")
            create_user(db, "admin", admin_pwd, "admin@localhost")
            print("[AutoNews] Usuario 'admin' creado. ¡Cambia la contraseña en el panel!")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: crear tablas y usuario admin inicial
    Base.metadata.create_all(bind=engine)
    _create_default_admin()
    yield
    # Shutdown: nada que limpiar por ahora


app = FastAPI(
    title="AutoNews Admin",
    description="Panel de administración para procesamiento automático de noticias",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "dev-secret-key-change-in-production"),
    session_cookie="autonews_sess",
    max_age=86400,      # 24 h
    same_site="lax",
    https_only=False,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(settings.router)
app.include_router(posts.router)
app.include_router(users.router)


@app.get("/health")
def health():
    db = SessionLocal()
    try:
        users = db.query(User).count()
        admin = db.query(User).filter(User.username == "admin").first()
        return {
            "status": "ok",
            "users_total": users,
            "admin_exists": admin is not None,
            "admin_active": admin.is_active if admin else None,
        }
    finally:
        db.close()
