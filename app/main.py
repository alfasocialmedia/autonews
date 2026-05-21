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
from app.routes import auth, dashboard, instagram, posts, publicaciones, rss, settings, users, whatsapp


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


def _migrate_wa_channels():
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    with engine.begin() as conn:
        if "whatsapp_channels" not in inspector.get_table_names():
            conn.execute(text("""
                CREATE TABLE whatsapp_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    jid VARCHAR(200) NOT NULL UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))


def _migrate_whatsapp():
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    with engine.begin() as conn:
        tables = inspector.get_table_names()
        if "whatsapp_settings" not in tables:
            conn.execute(text("""
                CREATE TABLE whatsapp_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evolution_api_url VARCHAR(300) DEFAULT 'http://localhost:8080',
                    evolution_api_key VARCHAR(300) DEFAULT '',
                    instance_name VARCHAR(100) DEFAULT 'botnews',
                    enabled BOOLEAN DEFAULT 0,
                    authorized_numbers TEXT DEFAULT '',
                    broadcast_enabled BOOLEAN DEFAULT 0,
                    broadcast_template TEXT DEFAULT '*{title}*\n\n{summary}\n\n{url}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME
                )
            """))
        if "whatsapp_groups" not in tables:
            conn.execute(text("""
                CREATE TABLE whatsapp_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    jid VARCHAR(200) NOT NULL UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))


def _migrate_elevenlabs():
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    with engine.begin() as conn:
        if "elevenlabs_settings" not in inspector.get_table_names():
            conn.execute(text("""
                CREATE TABLE elevenlabs_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    encrypted_api_key TEXT NOT NULL,
                    voice_id VARCHAR(100) DEFAULT 'pNInz6obpgDQGcFmaJgB',
                    model_id VARCHAR(100) DEFAULT 'eleven_multilingual_v2',
                    enabled BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME
                )
            """))


def _migrate_edge_tts():
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    with engine.begin() as conn:
        if "edge_tts_settings" not in inspector.get_table_names():
            conn.execute(text("""
                CREATE TABLE edge_tts_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    voice VARCHAR(100) DEFAULT 'com.ar',
                    enabled BOOLEAN DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME
                )
            """))


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
            if "feed_type" not in cols:
                conn.execute(text("ALTER TABLE rss_feeds ADD COLUMN feed_type VARCHAR(20) DEFAULT 'rss'"))
            if "wordpress_settings_id" not in cols:
                conn.execute(text("ALTER TABLE rss_feeds ADD COLUMN wordpress_settings_id INTEGER REFERENCES wordpress_settings(id) ON DELETE SET NULL"))
            if "wp_site_ids" not in cols:
                conn.execute(text("ALTER TABLE rss_feeds ADD COLUMN wp_site_ids TEXT"))
                # Migrar wordpress_settings_id existente a wp_site_ids
                conn.execute(text("""
                    UPDATE rss_feeds SET wp_site_ids = json_array(wordpress_settings_id)
                    WHERE wordpress_settings_id IS NOT NULL AND wp_site_ids IS NULL
                """))
        if "email_accounts" in tables:
            cols = [c["name"] for c in inspector.get_columns("email_accounts")]
            if "wp_site_ids" not in cols:
                conn.execute(text("ALTER TABLE email_accounts ADD COLUMN wp_site_ids TEXT"))
            if "publish_status" not in cols:
                conn.execute(text("ALTER TABLE email_accounts ADD COLUMN publish_status VARCHAR(20)"))
        if "whatsapp_settings" in tables:
            cols = [c["name"] for c in inspector.get_columns("whatsapp_settings")]
            if "name" not in cols:
                conn.execute(text("ALTER TABLE whatsapp_settings ADD COLUMN name VARCHAR(100) DEFAULT 'Principal'"))
            if "wordpress_settings_id" not in cols:
                conn.execute(text("ALTER TABLE whatsapp_settings ADD COLUMN wordpress_settings_id INTEGER REFERENCES wordpress_settings(id) ON DELETE SET NULL"))
            if "publish_mode" not in cols:
                conn.execute(text("ALTER TABLE whatsapp_settings ADD COLUMN publish_mode VARCHAR(20) DEFAULT 'both'"))
            if "rewrite_mode" not in cols:
                conn.execute(text("ALTER TABLE whatsapp_settings ADD COLUMN rewrite_mode VARCHAR(20) DEFAULT 'rewrite'"))
        if "whatsapp_groups" in tables:
            cols = [c["name"] for c in inspector.get_columns("whatsapp_groups")]
            if "wordpress_settings_id" not in cols:
                conn.execute(text("ALTER TABLE whatsapp_groups ADD COLUMN wordpress_settings_id INTEGER REFERENCES wordpress_settings(id) ON DELETE SET NULL"))
            if "whatsapp_settings_id" not in cols:
                conn.execute(text("ALTER TABLE whatsapp_groups ADD COLUMN whatsapp_settings_id INTEGER REFERENCES whatsapp_settings(id) ON DELETE CASCADE"))
                conn.execute(text("UPDATE whatsapp_groups SET whatsapp_settings_id = (SELECT id FROM whatsapp_settings LIMIT 1) WHERE whatsapp_settings_id IS NULL"))
        if "whatsapp_channels" in tables:
            cols = [c["name"] for c in inspector.get_columns("whatsapp_channels")]
            if "whatsapp_settings_id" not in cols:
                conn.execute(text("ALTER TABLE whatsapp_channels ADD COLUMN whatsapp_settings_id INTEGER REFERENCES whatsapp_settings(id) ON DELETE CASCADE"))
                conn.execute(text("UPDATE whatsapp_channels SET whatsapp_settings_id = (SELECT id FROM whatsapp_settings LIMIT 1) WHERE whatsapp_settings_id IS NULL"))
        if "posts" in tables:
            cols = [c["name"] for c in inspector.get_columns("posts")]
            if "source_name" not in cols:
                conn.execute(text("ALTER TABLE posts ADD COLUMN source_name VARCHAR(200)"))
            if "wordpress_settings_id" not in cols:
                conn.execute(text("ALTER TABLE posts ADD COLUMN wordpress_settings_id INTEGER REFERENCES wordpress_settings(id) ON DELETE SET NULL"))
            # Backfill: asignar sitio WP a posts existentes cruzando wp_link con site_url
            if "wordpress_settings" in tables:
                conn.execute(text("""
                    UPDATE posts SET wordpress_settings_id = (
                        SELECT ws.id FROM wordpress_settings ws
                        WHERE posts.wp_link LIKE ws.site_url || '%'
                        ORDER BY LENGTH(ws.site_url) DESC
                        LIMIT 1
                    ) WHERE wordpress_settings_id IS NULL
                      AND wp_link IS NOT NULL AND wp_link != ''
                """))
        if "groq_settings" in tables:
            cols = [c["name"] for c in inspector.get_columns("groq_settings")]
            if "provider" not in cols:
                conn.execute(text("ALTER TABLE groq_settings ADD COLUMN provider VARCHAR(50) DEFAULT 'groq'"))
            if "api_base_url" not in cols:
                conn.execute(text("ALTER TABLE groq_settings ADD COLUMN api_base_url VARCHAR(300)"))
        if "instagram_settings" in tables:
            ig_cols = [c["name"] for c in inspector.get_columns("instagram_settings")]
            if "gradient_color" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN gradient_color VARCHAR(10) DEFAULT '#000000'"))
            if "gradient_opacity" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN gradient_opacity INTEGER DEFAULT 200"))
            if "gradient_height" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN gradient_height INTEGER DEFAULT 480"))
            if "font_size" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN font_size INTEGER DEFAULT 62"))
            if "text_color" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN text_color VARCHAR(10) DEFAULT '#ffffff'"))
            if "banner_text" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN banner_text VARCHAR(300)"))
            if "banner_color" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN banner_color VARCHAR(10) DEFAULT '#e53935'"))
            if "banner_text_color" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN banner_text_color VARCHAR(10) DEFAULT '#ffffff'"))
            if "text_align" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN text_align VARCHAR(10) DEFAULT 'left'"))
            if "title_y_offset" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN title_y_offset INTEGER DEFAULT 0"))
            if "font_family" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN font_family VARCHAR(30) DEFAULT 'sans'"))
            if "text_bg_color" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN text_bg_color VARCHAR(10) DEFAULT '#000000'"))
            if "text_bg_opacity" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN text_bg_opacity INTEGER DEFAULT 0"))
            if "logo_size" not in ig_cols:
                conn.execute(text("ALTER TABLE instagram_settings ADD COLUMN logo_size INTEGER DEFAULT 180"))
        if "rss_feeds" in tables:
            rss_cols = [c["name"] for c in inspector.get_columns("rss_feeds")]
            if "instagram_settings_id" not in rss_cols:
                conn.execute(text("ALTER TABLE rss_feeds ADD COLUMN instagram_settings_id INTEGER REFERENCES instagram_settings(id) ON DELETE SET NULL"))
        if "email_accounts" in tables:
            email_cols = [c["name"] for c in inspector.get_columns("email_accounts")]
            if "instagram_settings_id" not in email_cols:
                conn.execute(text("ALTER TABLE email_accounts ADD COLUMN instagram_settings_id INTEGER REFERENCES instagram_settings(id) ON DELETE SET NULL"))
        import pathlib
        pathlib.Path("app/static/uploads/logos").mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import pathlib
    pathlib.Path("/app/data").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _migrate_whatsapp()
    _migrate_wa_channels()
    _migrate_elevenlabs()
    _migrate_edge_tts()
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
app.include_router(publicaciones.router)
app.include_router(users.router)
app.include_router(whatsapp.router)
app.include_router(instagram.router)


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
