"""
Worker autónomo: revisa correos IMAP cada 60 segundos, procesa con Groq y publica en WordPress.
Ejecutar con:  python -m app.worker
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import schedule
from dotenv import load_dotenv

load_dotenv()

from app.crypto import decrypt_value
from app.database import SessionLocal
from app.models import (
    CategoryMapping,
    EmailAccount,
    GroqSettings,
    Log,
    Post,
    ProcessedEmail,
    WordPressSettings,
)
from app.services.email_service import fetch_unread_emails
from app.services.groq_service import process_email_with_groq
from app.services.wordpress_service import create_post

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("worker")


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _log_db(db, level: str, message: str, source: str = "worker"):
    try:
        db.add(Log(level=level, message=message, source=source))
        db.commit()
    except Exception:
        db.rollback()


def _resolve_categories(db, wp_id: int, category_name: str) -> list[int]:
    if not category_name:
        return []
    mappings = (
        db.query(CategoryMapping)
        .filter(CategoryMapping.wordpress_settings_id == wp_id)
        .all()
    )
    for m in mappings:
        if m.keyword.lower() in category_name.lower():
            return [m.category_id]
    return []


# ──────────────────────────────────────────────────────────────────────────────
#  Core job
# ──────────────────────────────────────────────────────────────────────────────


def process_emails():
    log.info("▶ Iniciando ciclo de revisión de correos")
    db = SessionLocal()
    try:
        groq_cfg: GroqSettings | None = (
            db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
        )
        wp_cfg: WordPressSettings | None = (
            db.query(WordPressSettings).filter(WordPressSettings.is_active == True).first()
        )

        if not groq_cfg or not wp_cfg:
            log.warning("Falta configuración de Groq o WordPress. Saltando ciclo.")
            return

        groq_key = decrypt_value(groq_cfg.encrypted_api_key)
        wp_pwd = decrypt_value(wp_cfg.encrypted_app_password)

        accounts = db.query(EmailAccount).filter(EmailAccount.is_active == True).all()
        if not accounts:
            log.info("No hay cuentas de correo activas configuradas.")
            return

        for account in accounts:
            try:
                log.info(f"📬 Revisando: {account.email}")
                acc_pwd = decrypt_value(account.encrypted_password)

                new_emails = fetch_unread_emails(
                    account.imap_server,
                    account.imap_port,
                    account.username,
                    acc_pwd,
                )

                if not new_emails:
                    log.info(f"  Sin correos nuevos en {account.email}")
                    continue

                for mail_data in new_emails:
                    # Verificar duplicados
                    exists = (
                        db.query(ProcessedEmail)
                        .filter(ProcessedEmail.message_id == mail_data["message_id"])
                        .first()
                    )
                    if exists:
                        continue

                    # Guardar correo recibido
                    processed = ProcessedEmail(
                        email_account_id=account.id,
                        message_id=mail_data["message_id"],
                        sender=mail_data["sender"],
                        subject=mail_data["subject"],
                        body=mail_data["body"],
                        received_at=mail_data["received_at"],
                        status="received",
                    )
                    db.add(processed)
                    db.commit()
                    db.refresh(processed)

                    log.info(f"  📨 Nuevo correo: {mail_data['subject'][:80]}")
                    _log_db(db, "INFO", f"Correo recibido: {mail_data['subject'][:200]}")

                    # Procesar con Groq
                    try:
                        ai_result = process_email_with_groq(
                            groq_key,
                            groq_cfg.model,
                            groq_cfg.base_prompt,
                            mail_data["subject"],
                            mail_data["body"],
                        )

                        processed.ai_response = json.dumps(ai_result, ensure_ascii=False)
                        processed.status = "processed"
                        db.commit()

                        # Publicar en WordPress
                        category_ids = _resolve_categories(
                            db, wp_cfg.id, ai_result.get("category", "")
                        )

                        wp_post = create_post(
                            wp_cfg.site_url,
                            wp_cfg.api_user,
                            wp_pwd,
                            ai_result.get("title", mail_data["subject"]),
                            ai_result.get("content", mail_data["body"]),
                            wp_cfg.default_status,
                            category_ids,
                        )

                        db.add(
                            Post(
                                processed_email_id=processed.id,
                                wordpress_post_id=wp_post.get("id"),
                                title=ai_result.get("title", ""),
                                content=ai_result.get("content", ""),
                                category=ai_result.get("category", ""),
                                status=wp_cfg.default_status,
                                wp_link=wp_post.get("link", ""),
                            )
                        )
                        processed.status = "published"
                        db.commit()

                        msg = f"Publicado: {ai_result.get('title', '')[:150]}"
                        log.info(f"  ✅ {msg}")
                        _log_db(db, "INFO", msg)

                    except Exception as exc:
                        processed.status = "error"
                        processed.error_message = str(exc)
                        db.commit()
                        msg = f"Error procesando '{mail_data['subject'][:100]}': {exc}"
                        log.error(f"  ❌ {msg}")
                        _log_db(db, "ERROR", msg)

            except Exception as exc:
                msg = f"Error con cuenta {account.email}: {exc}"
                log.error(msg)
                _log_db(db, "ERROR", msg)

    except Exception as exc:
        log.error(f"Error crítico en el worker: {exc}")
    finally:
        db.close()

    log.info("⏹ Ciclo completado")


# ──────────────────────────────────────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────────────────────────────────────


def main():
    log.info("=" * 60)
    log.info("  AutoNews Worker — iniciado")
    log.info("  Intervalo: cada 60 segundos")
    log.info("=" * 60)

    schedule.every(60).seconds.do(process_emails)

    # Ejecutar inmediatamente al arrancar
    process_emails()

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
