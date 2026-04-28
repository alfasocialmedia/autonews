from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


# ── Usuarios ──────────────────────────────────────────────────────────────────

class UserBase(BaseModel):
    username: str
    email: Optional[str] = None


class UserCreate(UserBase):
    password: str


class UserOut(UserBase):
    id: int
    is_active: bool
    created_at: Optional[datetime]
    last_login: Optional[datetime]

    class Config:
        from_attributes = True


# ── Cuentas de correo ─────────────────────────────────────────────────────────

class EmailAccountBase(BaseModel):
    name: str
    email: str
    imap_server: str
    imap_port: int = 993
    username: str


class EmailAccountCreate(EmailAccountBase):
    password: str


class EmailAccountOut(EmailAccountBase):
    id: int
    is_active: bool
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── WordPress ─────────────────────────────────────────────────────────────────

class WordPressSettingsBase(BaseModel):
    name: str = "Principal"
    site_url: str
    api_user: str
    default_status: str = "draft"


class WordPressSettingsOut(WordPressSettingsBase):
    id: int
    is_active: bool

    class Config:
        from_attributes = True


# ── Groq ──────────────────────────────────────────────────────────────────────

class GroqSettingsBase(BaseModel):
    model: str = "llama-3.3-70b-versatile"
    base_prompt: str


class GroqSettingsOut(GroqSettingsBase):
    id: int
    is_active: bool

    class Config:
        from_attributes = True


# ── Correos procesados ────────────────────────────────────────────────────────

class ProcessedEmailOut(BaseModel):
    id: int
    sender: Optional[str]
    subject: Optional[str]
    status: str
    received_at: Optional[datetime]
    created_at: Optional[datetime]
    error_message: Optional[str]

    class Config:
        from_attributes = True


# ── Posts ─────────────────────────────────────────────────────────────────────

class PostOut(BaseModel):
    id: int
    title: Optional[str]
    category: Optional[str]
    status: str
    wp_link: Optional[str]
    wordpress_post_id: Optional[int]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── Respuestas API ────────────────────────────────────────────────────────────

class TestResult(BaseModel):
    success: bool
    message: str
