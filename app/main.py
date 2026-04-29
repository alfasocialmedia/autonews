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
from app.routes import auth, dashboard, posts, rss, settings, users


def _create_default_admin():
    db = SessionLocal()
    try:
        from app.auth import create_user
        from app.models import User

        if db.query(User).count() == 0:
            admin_pwd = os.getenv("ADMIN_PASSWORD", "admin123")
            u = create_user(db, "admin", admin_pwd, "admin@localhost")
            u.role = "admin"
            db.commit()
            print("[AutoNews] Usuario 'admin' creado. ¡Cambia la contraseña en el panel!")
    finally:
        db.close()


def _migrate_columns():
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    with engine.begin() as conn:
        tables = inspector.get_table_names()
        if "rss_feeds" in tables:
            cols = [c["name"] for c in inspector.get_columns("rss_feeds")]
            if "articles_per_check" not in cols:
                conn.execute(text("ALTER TABLE rss_feeds ADD COLUMN articles_per_check INTEGER DEFAULT 1"))
            if "keyword_filter" not in cols:
                conn.execute(text("ALTER TABLE rss_feeds ADD COLUMN keyword_filter TEXT"))
            if "wp_category_id" not in cols:
                conn.execute(text("ALTER TABLE rss_feeds ADD COLUMN wp_category_id INTEGER"))
            if "wp_category_name" not in cols:
                conn.execute(text("ALTER TABLE rss_feeds ADD COLUMN wp_category_name VARCHAR(100)"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    import pathlib
    pathlib.Path("/app/data").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _migrate_columns()
    _create_default_admin()
    from app.worker import start_background
    start_background()
    yield


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
app.include_router(rss.router)
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
