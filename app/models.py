from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, ForeignKey,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(150), unique=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    role = Column(String(20), default="editor")  # admin | editor
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login = Column(DateTime(timezone=True), nullable=True)


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(150), nullable=False)
    imap_server = Column(String(200), nullable=False)
    imap_port = Column(Integer, default=993)
    username = Column(String(150), nullable=False)
    encrypted_password = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    # JSON array de IDs: "[1,3]" = solo esos sitios, NULL = todos los activos
    wp_site_ids = Column(Text, nullable=True)
    # NULL = usar el default_status de cada sitio WordPress
    publish_status = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    processed_emails = relationship("ProcessedEmail", back_populates="email_account")


class WordPressSettings(Base):
    __tablename__ = "wordpress_settings"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), default="Principal")
    site_url = Column(String(300), nullable=False)
    api_user = Column(String(150), nullable=False)
    encrypted_app_password = Column(Text, nullable=False)
    default_status = Column(String(20), default="draft")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    category_mappings = relationship(
        "CategoryMapping", back_populates="wordpress_settings", cascade="all, delete-orphan"
    )


class CategoryMapping(Base):
    __tablename__ = "category_mappings"

    id = Column(Integer, primary_key=True, index=True)
    wordpress_settings_id = Column(
        Integer, ForeignKey("wordpress_settings.id", ondelete="CASCADE"), nullable=False
    )
    keyword = Column(String(100), nullable=False)
    category_id = Column(Integer, nullable=False)
    category_name = Column(String(100), nullable=False)

    wordpress_settings = relationship("WordPressSettings", back_populates="category_mappings")


class GroqSettings(Base):
    __tablename__ = "groq_settings"

    id = Column(Integer, primary_key=True, index=True)
    encrypted_api_key = Column(Text, nullable=False)
    model = Column(String(100), default="llama-3.3-70b-versatile")
    base_prompt = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    provider = Column(String(50), default="groq")
    api_base_url = Column(String(300), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ProcessedEmail(Base):
    __tablename__ = "processed_emails"

    id = Column(Integer, primary_key=True, index=True)
    email_account_id = Column(Integer, ForeignKey("email_accounts.id"), nullable=True)
    message_id = Column(String(400), unique=True, index=True)
    sender = Column(String(200))
    subject = Column(String(500))
    body = Column(Text)
    received_at = Column(DateTime(timezone=True))
    # received | processed | published | error
    status = Column(String(20), default="received", index=True)
    error_message = Column(Text, nullable=True)
    ai_response = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    email_account = relationship("EmailAccount", back_populates="processed_emails")
    posts = relationship("Post", back_populates="processed_email", cascade="all, delete-orphan")


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    processed_email_id = Column(Integer, ForeignKey("processed_emails.id"), nullable=True)
    wordpress_post_id = Column(Integer, nullable=True)
    title = Column(String(500))
    content = Column(Text)
    category = Column(String(100), nullable=True)
    status = Column(String(50), default="draft")
    wp_link = Column(String(600), nullable=True)
    source_name = Column(String(200), nullable=True)
    wordpress_settings_id = Column(
        Integer, ForeignKey("wordpress_settings.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    processed_email = relationship("ProcessedEmail", back_populates="posts")
    wordpress_settings = relationship("WordPressSettings", foreign_keys=[wordpress_settings_id])


class GoogleDriveSettings(Base):
    __tablename__ = "google_drive_settings"

    id = Column(Integer, primary_key=True, index=True)
    encrypted_api_key = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String(20), default="INFO", index=True)
    message = Column(Text, nullable=False)
    source = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RssFeed(Base):
    __tablename__ = "rss_feeds"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    url = Column(String(500), nullable=False)
    feed_type = Column(String(20), default="rss")   # "rss" | "web"
    is_active = Column(Boolean, default=True)
    check_interval_minutes = Column(Integer, default=60)
    articles_per_check = Column(Integer, default=1)
    max_articles_per_day = Column(Integer, default=5)
    keyword_filter = Column(Text, nullable=True)
    wp_category_id = Column(Integer, nullable=True)
    wp_category_name = Column(String(100), nullable=True)
    # wordpress_settings_id mantenido por compatibilidad; usar wp_site_ids para nuevas asignaciones
    wordpress_settings_id = Column(
        Integer, ForeignKey("wordpress_settings.id", ondelete="SET NULL"), nullable=True
    )
    # JSON array de IDs: "[1,3]" = solo esos sitios, NULL = todos los activos
    wp_site_ids = Column(Text, nullable=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    items = relationship("ProcessedRssItem", back_populates="rss_feed", cascade="all, delete-orphan")
    wordpress_settings = relationship("WordPressSettings", foreign_keys=[wordpress_settings_id])


class ProcessedRssItem(Base):
    __tablename__ = "processed_rss_items"

    id = Column(Integer, primary_key=True, index=True)
    rss_feed_id = Column(Integer, ForeignKey("rss_feeds.id"), nullable=False)
    guid = Column(String(500), unique=True, index=True)
    title = Column(String(500), nullable=True)
    link = Column(String(500), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(20), default="received", index=True)
    error_message = Column(Text, nullable=True)

    rss_feed = relationship("RssFeed", back_populates="items")


class ElevenLabsSettings(Base):
    __tablename__ = "elevenlabs_settings"

    id = Column(Integer, primary_key=True, index=True)
    encrypted_api_key = Column(Text, nullable=False)
    voice_id = Column(String(100), default="pNInz6obpgDQGcFmaJgB")
    model_id = Column(String(100), default="eleven_multilingual_v2")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class EdgeTTSSettings(Base):
    __tablename__ = "edge_tts_settings"

    id = Column(Integer, primary_key=True, index=True)
    voice = Column(String(100), default="com.ar")
    enabled = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class WhatsAppSettings(Base):
    __tablename__ = "whatsapp_settings"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), default="Principal")
    evolution_api_url = Column(String(300), default="http://localhost:8080")
    evolution_api_key = Column(String(300), default="")
    instance_name = Column(String(100), default="botnews")
    enabled = Column(Boolean, default=False)
    authorized_numbers = Column(Text, default="")
    broadcast_enabled = Column(Boolean, default=False)
    broadcast_template = Column(Text, default="*{title}*\n\n{summary}\n\n{url}")
    # "both" | "wordpress_only" | "whatsapp_only"
    publish_mode = Column(String(20), default="both")
    # "rewrite" | "title_only"
    rewrite_mode = Column(String(20), default="rewrite")
    # WordPress destino: NULL = publicar en todos los sitios activos
    wordpress_settings_id = Column(
        Integer, ForeignKey("wordpress_settings.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    wordpress_settings = relationship("WordPressSettings", foreign_keys=[wordpress_settings_id])
    groups = relationship("WhatsAppGroup", back_populates="whatsapp_settings", cascade="all, delete-orphan")
    channels = relationship("WhatsAppChannel", back_populates="whatsapp_settings", cascade="all, delete-orphan")


class WhatsAppGroup(Base):
    __tablename__ = "whatsapp_groups"

    id = Column(Integer, primary_key=True, index=True)
    jid = Column(String(200), unique=True, nullable=False)   # 1234567890-123@g.us
    name = Column(String(200), nullable=False)
    enabled = Column(Boolean, default=True)
    whatsapp_settings_id = Column(
        Integer, ForeignKey("whatsapp_settings.id", ondelete="CASCADE"), nullable=True
    )
    wordpress_settings_id = Column(
        Integer, ForeignKey("wordpress_settings.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    whatsapp_settings = relationship("WhatsAppSettings", back_populates="groups", foreign_keys=[whatsapp_settings_id])
    wordpress_settings = relationship("WordPressSettings", foreign_keys=[wordpress_settings_id])


class WhatsAppChannel(Base):
    __tablename__ = "whatsapp_channels"

    id = Column(Integer, primary_key=True, index=True)
    jid = Column(String(200), unique=True, nullable=False)   # 120363XXXXXXXXXX@newsletter
    name = Column(String(200), nullable=False)
    enabled = Column(Boolean, default=True)
    whatsapp_settings_id = Column(
        Integer, ForeignKey("whatsapp_settings.id", ondelete="CASCADE"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    whatsapp_settings = relationship("WhatsAppSettings", back_populates="channels", foreign_keys=[whatsapp_settings_id])


class InstagramSettings(Base):
    __tablename__ = "instagram_settings"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), default="Instagram")
    # Credenciales Graph API
    ig_user_id = Column(String(50), nullable=True)          # Instagram Business Account ID
    app_id = Column(String(50), nullable=True)
    encrypted_app_secret = Column(Text, nullable=True)
    encrypted_access_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    # Logo para superposición en imágenes
    logo_path = Column(String(300), nullable=True)          # ruta relativa en static/
    logo_position = Column(String(20), default="bottom-right")  # top-left|top-right|bottom-left|bottom-right
    # Estilo de imagen
    gradient_color = Column(String(10), default="#000000")
    gradient_opacity = Column(Integer, default=200)
    gradient_height = Column(Integer, default=480)
    font_size = Column(Integer, default=62)
    text_color = Column(String(10), default="#ffffff")
    # Control
    is_active = Column(Boolean, default=False)
    max_posts_per_day = Column(Integer, default=10)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
