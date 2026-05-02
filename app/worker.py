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
    ElevenLabsSettings,
    EmailAccount,
    GoogleDriveSettings,
    GroqSettings,
    Log,
    Post,
    ProcessedEmail,
    ProcessedRssItem,
    RssFeed,
    WordPressSettings,
)
from app.services.email_service import fetch_unread_emails
from app.services.groq_service import process_email_with_groq, process_rss_with_groq
from app.services.rss_service import fetch_rss_items, scrape_full_article
from app.services.wordpress_service import create_post, find_category_by_name, get_categories, get_or_create_category, get_or_create_tags, upload_audio, upload_media

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("worker")


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _matches_keyword_filter(keyword_filter: str | None, title: str, body: str = "") -> bool:
    """Devuelve True si el artículo pasa el filtro de palabras clave (o si no hay filtro)."""
    if not keyword_filter:
        return True
    haystack = (title + " " + body).lower()
    for kw in keyword_filter.split(","):
        kw = kw.strip()
        if kw and kw in haystack:
            return True
    return False


def _log_db(db, level: str, message: str, source: str = "worker"):
    try:
        db.add(Log(level=level, message=message, source=source))
        db.commit()
    except Exception:
        db.rollback()


def _fetch_wp_category_names(wp_sites: list) -> list[str]:
    """Obtiene los nombres de las categorías reales de WordPress (usa el primer sitio activo)."""
    for wp_cfg in wp_sites:
        try:
            wp_pwd = decrypt_value(wp_cfg.encrypted_app_password)
            cats = get_categories(wp_cfg.site_url, wp_cfg.api_user, wp_pwd)
            names = [c["name"] for c in cats if c.get("name")]
            if names:
                log.info("Categorías WP disponibles: %s", ", ".join(names))
                return names
        except Exception as exc:
            log.warning("No se pudieron obtener categorías de WP '%s': %s", wp_cfg.name, exc)
    return []


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

    # 2. Buscar en WP por nombre; si no existe, crearla automáticamente
    try:
        wp_pwd = decrypt_value(wp_cfg.encrypted_app_password)
        cat_id = get_or_create_category(wp_cfg.site_url, wp_cfg.api_user, wp_pwd, category_name)
        if cat_id:
            log.info("Categoría resuelta: '%s' → ID %s", category_name, cat_id)
            return [cat_id]
        log.warning("No se pudo resolver la categoría '%s' en WP — se usará la categoría por defecto", category_name)
    except Exception as exc:
        log.error("Error resolviendo categoría '%s': %s", category_name, exc)

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
        wp_categories = _fetch_wp_category_names(wp_sites)

        gdrive_cfg = db.query(GoogleDriveSettings).filter(GoogleDriveSettings.is_active == True).first()
        gdrive_api_key = decrypt_value(gdrive_cfg.encrypted_api_key) if gdrive_cfg else None

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
                            available_categories=wp_categories or None,
                            provider=groq_cfg.provider or "groq",
                            api_base_url=groq_cfg.api_base_url,
                        )

                        processed.ai_response = json.dumps(ai_result, ensure_ascii=False)
                        processed.status = "processed"
                        db.commit()

                        # Generar audio TTS una sola vez (antes del loop de sitios WP)
                        _email_audio = _generate_tts_audio(db, ai_result)

                        # Publicar en cada sitio WordPress activo
                        published_count = 0
                        for wp_cfg in wp_sites:
                            try:
                                wp_pwd = decrypt_value(wp_cfg.encrypted_app_password)
                                category_ids = _resolve_categories(
                                    db, wp_cfg, ai_result.get("category", "")
                                )

                                # Subir imagen de portada si existe (adjunto o URL en cuerpo)
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
                                        log.warning(f"  No se pudo subir imagen adjunta: {img_exc}")
                                elif mail_data.get("image_url"):
                                    resolved = _resolve_image_url(
                                        mail_data["image_url"],
                                        gdrive_api_key,
                                    )
                                    if resolved:
                                        log.info(f"  🔗 Descargando imagen desde URL: {resolved[:80]}")
                                        img_payload = _download_image(resolved)
                                        if img_payload:
                                            img_data, img_name, img_mime = img_payload
                                            try:
                                                featured_media_id = upload_media(
                                                    wp_cfg.site_url,
                                                    wp_cfg.api_user,
                                                    wp_pwd,
                                                    img_data,
                                                    img_name,
                                                    img_mime,
                                                )
                                            except Exception as img_exc:
                                                log.warning(f"  No se pudo subir imagen URL: {img_exc}")

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

                                # Subir audio y anteponer bloque de reproductor al contenido
                                _email_content = ai_result.get("content", mail_data["body"])
                                if _email_audio:
                                    _email_content = _prepend_audio(
                                        wp_cfg.site_url, wp_cfg.api_user, wp_pwd,
                                        _email_audio, ai_result.get("title", mail_data["subject"]),
                                        _email_content,
                                    )

                                wp_post = create_post(
                                    wp_cfg.site_url,
                                    wp_cfg.api_user,
                                    wp_pwd,
                                    ai_result.get("title", mail_data["subject"]),
                                    _email_content,
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


def _download_image(url: str, timeout: int = 25) -> tuple[bytes, str, str] | None:
    """Descarga una imagen desde URL. Devuelve (bytes, filename, mimetype) o None."""
    import httpx, mimetypes, re
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; AutoNews/1.0)"})
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        ext = mimetypes.guess_extension(ctype) or ".jpg"
        ext = ext.replace(".jpe", ".jpg")
        slug = re.sub(r"[^a-z0-9]", "-", url.split("/")[-1].split("?")[0].lower())[:40] or "portada"
        if not slug.endswith(ext):
            slug = slug.rstrip("-") + ext
        return resp.content, slug, ctype
    except Exception as exc:
        log.warning("No se pudo descargar imagen %s: %s", url, exc)
        return None


_CAT_IMG_HINTS: dict[str, str] = {
    "policiales":       "crime scene police investigation photojournalism",
    "política":         "government parliament politicians press conference",
    "economía":         "economy finance business stock market",
    "deportes":         "sports competition stadium crowd",
    "espectáculos":     "entertainment show stage performance",
    "salud":            "healthcare hospital medicine doctor",
    "tecnología":       "technology digital innovation computer",
    "educación":        "education school university students",
    "nacionales":       "argentina city urban landscape",
    "internacionales":  "world globe international diplomacy",
    "previsión social": "social welfare retirement elderly",
    "cultura":          "culture art museum exhibition",
    "sociedad":         "society community people street",
    "ciencia":          "science laboratory research discovery",
    "turismo":          "travel tourism landscape destination",
    "medio ambiente":   "environment nature ecology outdoor",
}


def _generate_fallback_image(title: str, category: str = "") -> str | None:
    """Genera una imagen con IA usando Pollinations.ai (gratuito, sin API key)."""
    import urllib.parse, random

    cat_hint = ""
    if category:
        for k, v in _CAT_IMG_HINTS.items():
            if k in category.lower():
                cat_hint = v
                break

    parts = [p for p in ["professional news photo editorial style", title[:120], cat_hint] if p]
    prompt = ", ".join(parts)
    encoded = urllib.parse.quote(prompt)
    seed = random.randint(1, 99999)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1200&height=630&seed={seed}&nologo=true&model=flux"
    )
    log.info("  🎨 Generando imagen IA para: %s", title[:70])
    return url


def _gdrive_list_images(folder_id: str, api_key: str, client) -> list[dict]:
    """Lista imágenes directamente dentro de una carpeta de Drive."""
    resp = client.get(
        "https://www.googleapis.com/drive/v3/files",
        params={
            "q": f"'{folder_id}' in parents and mimeType contains 'image/' and trashed = false",
            "key": api_key,
            "fields": "files(id,name,mimeType)",
            "pageSize": 5,
            "orderBy": "name",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("files", [])


def _gdrive_list_subfolders(folder_id: str, api_key: str, client) -> list[dict]:
    """Lista subcarpetas directamente dentro de una carpeta de Drive."""
    resp = client.get(
        "https://www.googleapis.com/drive/v3/files",
        params={
            "q": f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            "key": api_key,
            "fields": "files(id,name)",
            "pageSize": 10,
            "orderBy": "name",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("files", [])


def _resolve_gdrive_folder(folder_id: str, api_key: str) -> str | None:
    """Busca la primera imagen en una carpeta pública de Drive (incluyendo subcarpetas)."""
    import httpx
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            # 1. Buscar imágenes directamente en la carpeta raíz
            images = _gdrive_list_images(folder_id, api_key, client)

            # 2. Si no hay imágenes directas, buscar en subcarpetas (un nivel)
            if not images:
                subfolders = _gdrive_list_subfolders(folder_id, api_key, client)
                for sub in subfolders:
                    images = _gdrive_list_images(sub["id"], api_key, client)
                    if images:
                        log.info("  📁 Imágenes encontradas en subcarpeta: %s", sub["name"])
                        break

            if not images:
                log.warning("  📁 Drive folder %s: sin imágenes (ni en subcarpetas)", folder_id)
                return None

            file_id = images[0]["id"]
            log.info("  📁 Drive: primera imagen → %s (%s)", images[0]["name"], file_id)
            # Usar el endpoint de la API con key en vez de uc?export=download (más confiable)
            return f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={api_key}"

    except Exception as exc:
        log.warning("  📁 No se pudo listar carpeta Drive %s: %s", folder_id, exc)
    return None


def _resolve_image_url(raw_url: str, gdrive_api_key: str | None) -> str | None:
    """Convierte cualquier URL de imagen (incluyendo gdrive-folder:ID) en una URL descargable."""
    if not raw_url:
        return None
    if raw_url.startswith("gdrive-folder:"):
        folder_id = raw_url.split(":", 1)[1]
        if not gdrive_api_key:
            log.warning("  📁 Carpeta Drive detectada pero GOOGLE_DRIVE_API_KEY no está configurada")
            return None
        return _resolve_gdrive_folder(folder_id, gdrive_api_key)
    return raw_url


def _generate_tts_audio(db, ai_result: dict) -> bytes | None:
    """Genera audio MP3 con ElevenLabs (prioridad) o Edge TTS (fallback gratuito). Devuelve bytes o None."""
    from app.services.elevenlabs_service import strip_html
    plain_text = strip_html(ai_result.get("content", ""))
    if not plain_text:
        return None

    # Intentar ElevenLabs primero
    try:
        el_cfg = db.query(ElevenLabsSettings).filter(ElevenLabsSettings.enabled == True).first()
        if el_cfg:
            from app.services.elevenlabs_service import generate_audio as el_generate
            el_key = decrypt_value(el_cfg.encrypted_api_key)
            audio = el_generate(plain_text, el_key, el_cfg.voice_id, el_cfg.model_id)
            log.info("🔊 Audio ElevenLabs generado: %d bytes", len(audio))
            return audio
    except Exception as exc:
        log.warning("ElevenLabs TTS error, intentando Edge TTS: %s", exc)

    # Fallback: Edge TTS gratuito
    try:
        from app.models import EdgeTTSSettings
        from app.services.edge_tts_service import generate_audio as edge_generate
        edge_cfg = db.query(EdgeTTSSettings).filter(EdgeTTSSettings.enabled == True).first()
        if edge_cfg:
            audio = edge_generate(plain_text, edge_cfg.voice)
            log.info("🔊 Audio Edge TTS generado: %d bytes", len(audio))
            return audio
    except Exception as exc:
        log.warning("Edge TTS error (artículo se publicará sin audio): %s", exc)

    return None


def _prepend_audio(
    site_url: str, api_user: str, wp_pwd: str,
    audio_bytes: bytes, title: str, content: str,
) -> str:
    """Sube el MP3 al media de WP e inyecta el bloque de audio al inicio del contenido."""
    import re as _re
    try:
        slug = _re.sub(r"[^a-z0-9]", "-", title.lower())[:50].strip("-") or "audio"
        result = upload_audio(site_url, api_user, wp_pwd, audio_bytes, f"{slug}.mp3")
        if result:
            _, audio_url = result
            block = (
                "<!-- wp:audio -->\n"
                f'<figure class="wp-block-audio"><audio controls src="{audio_url}" preload="metadata"></audio>'
                "<figcaption>Escuchar nota</figcaption></figure>\n"
                "<!-- /wp:audio -->\n\n"
            )
            return block + content
    except Exception as exc:
        log.warning("No se pudo adjuntar audio en %s: %s", site_url, exc)
    return content


def _publish_ai_result(db, ai_result: dict, wp_sites, image_url: str | None = None, source_name: str | None = None):
    """Publica un resultado de Groq en todos los sitios WP activos. Devuelve cantidad publicada."""
    published_count = 0

    # Descargar imagen una sola vez para todos los sitios
    img_payload = None
    if image_url:
        img_payload = _download_image(image_url)

    # Generar audio TTS una sola vez para todos los sitios (evita facturar dos veces)
    audio_bytes = _generate_tts_audio(db, ai_result)

    for wp_cfg in wp_sites:
        try:
            wp_pwd = decrypt_value(wp_cfg.encrypted_app_password)

            # Categoría forzada por el feed tiene prioridad sobre la detectada por Groq
            forced_id = ai_result.get("_forced_category_id")
            if forced_id:
                category_ids = [forced_id]
            else:
                category_ids = _resolve_categories(db, wp_cfg, ai_result.get("category", ""))

            tag_ids = []
            raw_tags = ai_result.get("tags", [])
            if isinstance(raw_tags, list) and raw_tags:
                try:
                    tag_ids = get_or_create_tags(wp_cfg.site_url, wp_cfg.api_user, wp_pwd, raw_tags)
                except Exception:
                    pass

            featured_media_id = None
            if img_payload:
                try:
                    img_data, img_name, img_mime = img_payload
                    featured_media_id = upload_media(
                        wp_cfg.site_url, wp_cfg.api_user, wp_pwd,
                        img_data, img_name, img_mime,
                    )
                except Exception as exc:
                    log.warning("No se pudo subir imagen a %s: %s", wp_cfg.name, exc)

            # Subir audio y anteponer bloque de reproductor al contenido
            content = ai_result.get("content", "")
            if audio_bytes:
                content = _prepend_audio(
                    wp_cfg.site_url, wp_cfg.api_user, wp_pwd,
                    audio_bytes, ai_result.get("title", ""), content,
                )

            wp_post = create_post(
                wp_cfg.site_url,
                wp_cfg.api_user,
                wp_pwd,
                ai_result.get("title", ""),
                content,
                wp_cfg.default_status,
                category_ids,
                featured_media_id,
                excerpt=ai_result.get("summary", ""),
                tag_ids=tag_ids,
                keyphrase=ai_result.get("keyphrase", ""),
            )

            db.add(
                Post(
                    processed_email_id=None,
                    wordpress_post_id=wp_post.get("id"),
                    title=ai_result.get("title", ""),
                    content=ai_result.get("content", ""),
                    category=ai_result.get("category", ""),
                    status=wp_cfg.default_status,
                    wp_link=wp_post.get("link", ""),
                    source_name=source_name,
                )
            )
            db.commit()
            published_count += 1

            # Difusión WhatsApp tras primera publicación exitosa
            if published_count == 1:
                _broadcast_whatsapp(db, ai_result, wp_post.get("link", ""))

        except Exception as exc:
            log.error("Error publicando RSS en %s: %s", wp_cfg.name, exc)
    return published_count


def _broadcast_whatsapp(db, ai_result: dict, wp_url: str):
    """Envía el artículo recién publicado a los grupos de WhatsApp configurados."""
    try:
        from app.models import WhatsAppSettings, WhatsAppGroup
        from app.services.whatsapp_service import send_text

        s = db.query(WhatsAppSettings).first()
        if not s or not s.broadcast_enabled or not s.evolution_api_url or not s.evolution_api_key:
            return

        groups = db.query(WhatsAppGroup).filter(WhatsAppGroup.enabled == True).all()
        if not groups:
            return

        title = ai_result.get("title", "")
        summary = ai_result.get("summary", "")
        template = s.broadcast_template or "*{title}*\n\n{summary}\n\n{url}"
        text = template.replace("{title}", title).replace("{summary}", summary).replace("{url}", wp_url)

        for g in groups:
            send_text(s.evolution_api_url, s.evolution_api_key, s.instance_name, g.jid, text)
            log.info("WA difusión → %s (%s)", g.name, g.jid)
    except Exception as exc:
        log.warning("_broadcast_whatsapp error: %s", exc)


def process_rss_feeds():
    """Revisa los feeds RSS activos y publica artículos nuevos según su configuración."""
    from datetime import timedelta, timezone as tz

    TZ_AR = tz(timedelta(hours=-3))  # Argentina UTC-3, sin horario de verano

    log.info("▶ Revisando feeds RSS")
    db = SessionLocal()
    try:
        groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
        wp_sites = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).all()

        if not groq_cfg or not wp_sites:
            log.warning("RSS: Groq o WordPress no configurados/activos — saltando feeds")
            return

        feeds = db.query(RssFeed).filter(RssFeed.is_active == True).all()
        if not feeds:
            return

        groq_key = decrypt_value(groq_cfg.encrypted_api_key)
        wp_categories = _fetch_wp_category_names(wp_sites)
        now = datetime.now(TZ_AR)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        for feed in feeds:
            try:
                # Verificar si corresponde revisar este feed según su intervalo
                if feed.last_checked_at:
                    last = feed.last_checked_at
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=tz.utc)
                    next_check = last + timedelta(minutes=feed.check_interval_minutes)
                    if now < next_check:
                        continue

                # Contar publicaciones de hoy para este feed
                published_today = db.query(ProcessedRssItem).filter(
                    ProcessedRssItem.rss_feed_id == feed.id,
                    ProcessedRssItem.status == "published",
                    ProcessedRssItem.processed_at >= today_start,
                ).count()

                # Cutoff: solo ítems publicados después del último chequeo.
                # Para feeds nuevos (sin historial), usar las últimas 48h.
                prev_checked = feed.last_checked_at
                if prev_checked and prev_checked.tzinfo is None:
                    prev_checked = prev_checked.replace(tzinfo=tz.utc)
                cutoff = prev_checked if prev_checked else (now - timedelta(hours=48))

                feed.last_checked_at = now
                db.commit()

                if published_today >= feed.max_articles_per_day:
                    log.info("Feed '%s': límite diario alcanzado (%d/%d)", feed.name, published_today, feed.max_articles_per_day)
                    continue

                log.info("📡 Revisando feed: %s", feed.name)
                items = fetch_rss_items(feed.url)

                published_this_check = 0
                articles_per_check = feed.articles_per_check or 1

                for item in items:
                    if published_today >= feed.max_articles_per_day:
                        break
                    if published_this_check >= articles_per_check:
                        break

                    # Saltar si ya fue procesado
                    if db.query(ProcessedRssItem).filter(ProcessedRssItem.guid == item["guid"]).first():
                        continue

                    # Saltar ítems anteriores al último chequeo (artículos viejos / comentarios)
                    item_date = item.get("published_at")
                    if item_date is not None:
                        if item_date.tzinfo is None:
                            item_date = item_date.replace(tzinfo=tz.utc)
                        if item_date < cutoff:
                            continue

                    # Aplicar filtro de palabras clave sobre el título (rápido, sin scrapear aún)
                    if not _matches_keyword_filter(feed.keyword_filter, item["title"], item["body"]):
                        log.info("  ⏭ Descartado por filtro: %s", item["title"][:80])
                        # Marcar como visto para no revisarlo de nuevo
                        db.add(ProcessedRssItem(
                            rss_feed_id=feed.id,
                            guid=item["guid"],
                            title=item["title"],
                            link=item["link"],
                            published_at=item["published_at"],
                            status="skipped",
                        ))
                        db.commit()
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
                        image_url = item.get("image_url")

                        # Si el RSS solo trae un excerpt corto, scrapear el artículo completo
                        if item.get("needs_scraping") and item["link"]:
                            log.info("  🔍 Scrapeando artículo completo: %s", item["link"][:80])
                            scraped_text, scraped_img = scrape_full_article(item["link"])
                            if scraped_text:
                                body = scraped_text
                                # Re-verificar filtro con el texto completo del artículo
                                if not _matches_keyword_filter(feed.keyword_filter, item["title"], body):
                                    log.info("  ⏭ Descartado por filtro (cuerpo): %s", item["title"][:80])
                                    rss_item.status = "skipped"
                                    db.commit()
                                    continue
                            # og:image del artículo siempre es mejor que el thumbnail del RSS
                            if scraped_img:
                                image_url = scraped_img
                        elif item["link"]:
                            # Aunque el contenido sea completo, la og:image del artículo
                            # siempre es de mayor resolución que el thumbnail del feed RSS
                            _, scraped_img = scrape_full_article(item["link"])
                            if scraped_img:
                                image_url = scraped_img
                                log.info("  🖼 og:image scrapeada: %s", scraped_img[:80])

                        ai_result = process_rss_with_groq(
                            groq_key,
                            groq_cfg.model,
                            groq_cfg.base_prompt,
                            item["title"],
                            body,
                            available_categories=wp_categories or None,
                            provider=groq_cfg.provider or "groq",
                            api_base_url=groq_cfg.api_base_url,
                        )

                        # Categoría forzada por el feed (sobreescribe la de Groq)
                        if feed.wp_category_id:
                            ai_result["_forced_category_id"] = feed.wp_category_id
                            ai_result["_forced_category_name"] = feed.wp_category_name or ""
                        elif feed.wp_category_name and not feed.wp_category_id:
                            ai_result["category"] = feed.wp_category_name

                        # Si no hay imagen, generar una con IA relacionada al tema
                        if not image_url:
                            image_url = _generate_fallback_image(
                                ai_result.get("title", item["title"]),
                                ai_result.get("category", ""),
                            )

                        rss_item.status = "processed"
                        db.commit()

                        count = _publish_ai_result(db, ai_result, wp_sites, image_url=image_url, source_name=feed.name)
                        rss_item.status = "published" if count > 0 else "error"
                        db.commit()

                        if count > 0:
                            published_today += 1
                            published_this_check += 1
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


def publish_rss_item_now(db, feed: RssFeed, item: dict) -> dict:
    """Publica un ítem RSS específico de inmediato desde el panel de administración.
    Devuelve {'title': ..., 'wp_link': ...} o lanza ValueError."""
    groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
    wp_sites = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).all()
    if not groq_cfg:
        raise ValueError("Groq no configurado — actívalo en Configuración → Groq IA")
    if not wp_sites:
        raise ValueError("WordPress no configurado — agrega un sitio en Configuración → WordPress")

    groq_key = decrypt_value(groq_cfg.encrypted_api_key)
    wp_categories = _fetch_wp_category_names(wp_sites)

    body = item["body"]
    image_url = item.get("image_url")

    if item.get("needs_scraping") and item["link"]:
        log.info("[manual] Scrapeando artículo: %s", item["link"][:80])
        scraped_text, scraped_img = scrape_full_article(item["link"])
        if scraped_text:
            body = scraped_text
        if scraped_img:
            image_url = scraped_img
    elif item["link"]:
        _, scraped_img = scrape_full_article(item["link"])
        if scraped_img:
            image_url = scraped_img

    ai_result = process_rss_with_groq(
        groq_key, groq_cfg.model, groq_cfg.base_prompt,
        item["title"], body,
        available_categories=wp_categories or None,
        provider=groq_cfg.provider or "groq",
        api_base_url=groq_cfg.api_base_url,
    )

    if feed.wp_category_id:
        ai_result["_forced_category_id"] = feed.wp_category_id
        ai_result["_forced_category_name"] = feed.wp_category_name or ""
    elif feed.wp_category_name and not feed.wp_category_id:
        ai_result["category"] = feed.wp_category_name

    if not image_url:
        image_url = _generate_fallback_image(
            ai_result.get("title", item["title"]),
            ai_result.get("category", ""),
        )

    # Registrar en BD (o reutilizar si ya existe)
    rss_item = db.query(ProcessedRssItem).filter(ProcessedRssItem.guid == item["guid"]).first()
    if not rss_item:
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

    count = _publish_ai_result(db, ai_result, wp_sites, image_url=image_url, source_name=feed.name)

    if count > 0:
        rss_item.status = "published"
        db.commit()
        post = db.query(Post).filter(Post.source_name == feed.name).order_by(Post.id.desc()).first()
        return {
            "title": ai_result.get("title", item["title"]),
            "wp_link": post.wp_link if post else "",
        }

    rss_item.status = "error"
    db.commit()
    raise ValueError("No se pudo publicar en ningún sitio WordPress")


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
