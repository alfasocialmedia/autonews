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
    ProcessedRssItem,
    RssFeed,
    WordPressSettings,
)
from app.services.email_service import fetch_unread_emails
from app.services.groq_service import process_email_with_groq
from app.services.rss_service import fetch_rss_items
from app.services.wordpress_service import create_post, find_category_by_name, get_or_create_tags, upload_media

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


def _resolve_categories(db, wp_cfg, category_name: str) -> list[int]:
    if not category_name:
        return []

    import unicodedata

    def normalize(s: str) -> str:
        return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()

    norm_cat = normalize(category_name)

    # 1. Buscar por keyword en los mapeos manuales
    mappings = (
        db.query(CategoryMapping)
        .filter(CategoryMapping.wordpress_settings_id == wp_cfg.id)
        .all()
    )
    for m in mappings:
        if normalize(m.keyword) in norm_cat or norm_cat in normalize(m.category_name):
            return [m.category_id]

    # 2. Buscar directamente en WP por nombre de categoría
    try:
        wp_pwd = decrypt_value(wp_cfg.encrypted_app_password)
        cat_id = find_category_by_name(wp_cfg.site_url, wp_cfg.api_user, wp_pwd, category_name)
        if cat_id:
            return [cat_id]
    except Exception:
        pass

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
        wp_sites: list[WordPressSettings] = (
            db.query(WordPressSettings).filter(WordPressSettings.is_active == True).all()
        )

        if not groq_cfg:
            msg = "Groq no configurado — ve a Configuración → Groq IA y guarda tu API Key."
            log.warning(msg)
            _log_db(db, "WARN", msg)
            return

        if not wp_sites:
            msg = "No hay sitios WordPress activos — ve a Configuración → WordPress y agrega uno."
            log.warning(msg)
            _log_db(db, "WARN", msg)
            return

        groq_key = decrypt_value(groq_cfg.encrypted_api_key)

        accounts = db.query(EmailAccount).filter(EmailAccount.is_active == True).all()
        if not accounts:
            msg = "No hay cuentas de correo activas configuradas."
            log.info(msg)
            _log_db(db, "INFO", msg)
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
                    _log_db(db, "INFO", f"Sin correos nuevos en {account.email}")
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

                    # Procesar con Groq (una sola vez por correo)
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

                        # Publicar en cada sitio WordPress activo
                        published_count = 0
                        for wp_cfg in wp_sites:
                            try:
                                wp_pwd = decrypt_value(wp_cfg.encrypted_app_password)
                                category_ids = _resolve_categories(
                                    db, wp_cfg, ai_result.get("category", "")
                                )

                                # Subir imagen de portada si existe
                                featured_media_id = None
                                if mail_data.get("image_data"):
                                    try:
                                        featured_media_id = upload_media(
                                            wp_cfg.site_url,
                                            wp_cfg.api_user,
                                            wp_pwd,
                                            mail_data["image_data"],
                                            mail_data.get("image_filename") or "portada.jpg",
                                            mail_data.get("image_mime") or "image/jpeg",
                                        )
                                    except Exception as img_exc:
                                        log.warning(f"  No se pudo subir imagen: {img_exc}")

                                # Crear etiquetas
                                tag_ids = []
                                raw_tags = ai_result.get("tags", [])
                                if isinstance(raw_tags, list) and raw_tags:
                                    try:
                                        tag_ids = get_or_create_tags(
                                            wp_cfg.site_url, wp_cfg.api_user, wp_pwd, raw_tags
                                        )
                                    except Exception:
                                        pass

                                wp_post = create_post(
                                    wp_cfg.site_url,
                                    wp_cfg.api_user,
                                    wp_pwd,
                                    ai_result.get("title", mail_data["subject"]),
                                    ai_result.get("content", mail_data["body"]),
                                    wp_cfg.default_status,
                                    category_ids,
                                    featured_media_id,
                                    excerpt=ai_result.get("summary", ""),
                                    tag_ids=tag_ids,
                                    keyphrase=ai_result.get("keyphrase", ""),
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
                                db.commit()
                                published_count += 1

                                msg = f"Publicado en {wp_cfg.name}: {ai_result.get('title', '')[:120]}"
                                log.info(f"  ✅ {msg}")
                                _log_db(db, "INFO", msg)

                            except Exception as exc:
                                msg = f"Error publicando en {wp_cfg.name}: {exc}"
                                log.error(f"  ❌ {msg}")
                                _log_db(db, "ERROR", msg)

                        processed.status = "published" if published_count > 0 else "error"
                        db.commit()

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


def _publish_ai_result(db, ai_result: dict, wp_sites, groq_cfg=None):
    """Publica un resultado de Groq en todos los sitios WP activos. Devuelve cantidad publicada."""
    published_count = 0
    for wp_cfg in wp_sites:
        try:
            wp_pwd = decrypt_value(wp_cfg.encrypted_app_password)
            category_ids = _resolve_categories(db, wp_cfg, ai_result.get("category", ""))

            tag_ids = []
            raw_tags = ai_result.get("tags", [])
            if isinstance(raw_tags, list) and raw_tags:
                try:
                    tag_ids = get_or_create_tags(wp_cfg.site_url, wp_cfg.api_user, wp_pwd, raw_tags)
                except Exception:
                    pass

            create_post(
                wp_cfg.site_url,
                wp_cfg.api_user,
                wp_pwd,
                ai_result.get("title", ""),
                ai_result.get("content", ""),
                wp_cfg.default_status,
                category_ids,
                None,
                excerpt=ai_result.get("summary", ""),
                tag_ids=tag_ids,
                keyphrase=ai_result.get("keyphrase", ""),
            )
            published_count += 1
        except Exception as exc:
            log.error("Error publicando RSS en %s: %s", wp_cfg.name, exc)
    return published_count


def process_rss_feeds():
    """Revisa los feeds RSS activos y publica artículos nuevos según su configuración."""
    from datetime import timedelta, timezone as tz

    log.info("▶ Revisando feeds RSS")
    db = SessionLocal()
    try:
        groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
        wp_sites = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).all()

        if not groq_cfg or not wp_sites:
            return

        feeds = db.query(RssFeed).filter(RssFeed.is_active == True).all()
        if not feeds:
            return

        groq_key = decrypt_value(groq_cfg.encrypted_api_key)
        now = datetime.now(tz.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        for feed in feeds:
            try:
                # Verificar si corresponde revisar este feed según su intervalo
                if feed.last_checked_at:
                    next_check = feed.last_checked_at + timedelta(minutes=feed.check_interval_minutes)
                    if now < next_check:
                        continue

                # Contar publicaciones de hoy para este feed
                published_today = db.query(ProcessedRssItem).filter(
                    ProcessedRssItem.rss_feed_id == feed.id,
                    ProcessedRssItem.status == "published",
                    ProcessedRssItem.processed_at >= today_start,
                ).count()

                feed.last_checked_at = now
                db.commit()

                if published_today >= feed.max_articles_per_day:
                    log.info("Feed '%s': límite diario alcanzado (%d/%d)", feed.name, published_today, feed.max_articles_per_day)
                    continue

                log.info("📡 Revisando feed: %s", feed.name)
                items = fetch_rss_items(feed.url)

                for item in items:
                    if published_today >= feed.max_articles_per_day:
                        break

                    # Saltar si ya fue procesado
                    if db.query(ProcessedRssItem).filter(ProcessedRssItem.guid == item["guid"]).first():
                        continue

                    rss_item = ProcessedRssItem(
                        rss_feed_id=feed.id,
                        guid=item["guid"],
                        title=item["title"],
                        link=item["link"],
                        published_at=item["published_at"],
                        status="received",
                    )
                    db.add(rss_item)
                    db.commit()
                    db.refresh(rss_item)

                    log.info("  📰 Nuevo ítem RSS: %s", item["title"][:80])
                    _log_db(db, "INFO", f"[RSS] {feed.name}: {item['title'][:200]}", source="rss")

                    try:
                        body = item["body"]
                        if item["link"]:
                            body = f"Fuente original: {item['link']}\n\n{body}"

                        ai_result = process_email_with_groq(
                            groq_key,
                            groq_cfg.model,
                            groq_cfg.base_prompt,
                            item["title"],
                            body,
                        )
                        rss_item.status = "processed"
                        db.commit()

                        count = _publish_ai_result(db, ai_result, wp_sites)
                        rss_item.status = "published" if count > 0 else "error"
                        db.commit()

                        if count > 0:
                            published_today += 1
                            msg = f"[RSS] Publicado desde {feed.name}: {ai_result.get('title', '')[:120]}"
                            log.info("  ✅ %s", msg)
                            _log_db(db, "INFO", msg, source="rss")

                    except Exception as exc:
                        rss_item.status = "error"
                        rss_item.error_message = str(exc)
                        db.commit()
                        log.error("  ❌ Error procesando '%s': %s", item["title"][:80], exc)
                        _log_db(db, "ERROR", f"[RSS] Error en {feed.name}: {exc}", source="rss")

            except Exception as exc:
                log.error("Error con feed '%s': %s", feed.name, exc)
                _log_db(db, "ERROR", f"[RSS] Error con feed {feed.name}: {exc}", source="rss")

    except Exception as exc:
        log.error("Error crítico en process_rss_feeds: %s", exc)
    finally:
        db.close()

    log.info("⏹ Feeds RSS procesados")


def main():
    log.info("=" * 60)
    log.info("  AutoNews Worker — iniciado")
    log.info("  Emails: cada 60 s  |  RSS: cada 5 min")
    log.info("=" * 60)

    schedule.every(60).seconds.do(process_emails)
    schedule.every(5).minutes.do(process_rss_feeds)

    process_emails()
    process_rss_feeds()

    while True:
        schedule.run_pending()
        time.sleep(1)


def start_background():
    """Arranca el worker en un hilo daemon (usado desde main.py)."""
    import threading
    t = threading.Thread(target=main, daemon=True, name="autonews-worker")
    t.start()
    log.info("Worker iniciado en background thread")


if __name__ == "__main__":
    main()
