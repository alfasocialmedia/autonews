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
    source_name = Column(String(200), nullable=True)  # nombre del feed RSS o cuenta email
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    processed_email = relationship("ProcessedEmail", back_populates="posts")


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
    is_active = Column(Boolean, default=True)
    check_interval_minutes = Column(Integer, default=60)
    articles_per_check = Column(Integer, default=1)
    max_articles_per_day = Column(Integer, default=5)
    # Filtro: solo procesa artículos cuyo título/body contenga alguna de estas palabras (CSV)
    keyword_filter = Column(Text, nullable=True)
    # Categoría WP forzada: si se define, ignora la categoría que detecta Groq
    wp_category_id = Column(Integer, nullable=True)
    wp_category_name = Column(String(100), nullable=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    items = relationship("ProcessedRssItem", back_populates="rss_feed", cascade="all, delete-orphan")


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
