"""
Worker autónomo: revisa correos IMAP cada 60 segundos, procesa con Groq y publica en WordPress.
Ejecutar con:  python -m app.worker
"""
from __future__ import annotations

import json
import logging
import re
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
    InstagramSettings,
    Log,
    Post,
    ProcessedEmail,
    ProcessedRssItem,
    RssFeed,
    WordPressSettings,
)
from app.services.email_service import fetch_unread_emails
from app.services.groq_service import process_email_with_groq, process_rss_with_groq
from app.services.rss_service import fetch_rss_items, scrape_full_article, scrape_category_page, _is_garbled
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


def _check_content_quality(ai_result: dict, source_len: int) -> tuple[bool, str]:
    """Verificación automática de calidad antes de publicar.
    Devuelve (ok, motivo). Si ok=False el artículo NO se publica."""
    import re as _re
    content = ai_result.get("content", "")
    title = ai_result.get("title", "")

    # Título mínimo
    if len(title) < 25:
        return False, f"título demasiado corto ({len(title)} chars)"

    # Contenido mínimo
    plain = _re.sub(r"<[^>]+>", " ", content)
    words = [w for w in plain.split() if len(w) > 1]
    word_count = len(words)

    if word_count < 40:
        return False, f"contenido muy corto ({word_count} palabras)"

    # Párrafos con etiqueta <p>
    p_count = content.count("<p>")
    if p_count < 2:
        return False, f"sin párrafos HTML correctos (solo {p_count} <p>)"

    # Si la fuente era larga, exigir más contenido generado
    if source_len > 1500 and word_count < 150:
        return False, f"contenido incompleto para fuente de {source_len} chars: solo {word_count} palabras"

    return True, f"ok ({word_count} palabras, {p_count} párrafos)"


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

                        # Publicar solo en los sitios asignados a esta cuenta (o todos si no tiene asignación)
                        account_wp_sites = (
                            [s for s in wp_sites if s.id in json.loads(account.wp_site_ids)]
                            if account.wp_site_ids
                            else wp_sites
                        )
                        published_count = 0
                        for wp_cfg in account_wp_sites:
                            try:
                                wp_pwd = decrypt_value(wp_cfg.encrypted_app_password)
                                category_ids = _resolve_categories(
                                    db, wp_cfg, ai_result.get("category", "")
                                )

                                # Subir imagen de portada si existe (adjunto o URL en cuerpo)
                                featured_media_id = None
                                email_media_url = ""
                                if mail_data.get("image_data"):
                                    try:
                                        media_result = upload_media(
                                            wp_cfg.site_url,
                                            wp_cfg.api_user,
                                            wp_pwd,
                                            mail_data["image_data"],
                                            mail_data.get("image_filename") or "portada.jpg",
                                            mail_data.get("image_mime") or "image/jpeg",
                                        )
                                        if media_result:
                                            featured_media_id, email_media_url = media_result
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
                                                media_result = upload_media(
                                                    wp_cfg.site_url,
                                                    wp_cfg.api_user,
                                                    wp_pwd,
                                                    img_data,
                                                    img_name,
                                                    img_mime,
                                                )
                                                if media_result:
                                                    featured_media_id, email_media_url = media_result
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

                                _email_content = ai_result.get("content", mail_data["body"])
                                if _email_audio:
                                    _email_content = _prepend_audio(
                                        wp_cfg.site_url, wp_cfg.api_user, wp_pwd,
                                        _email_audio, ai_result.get("title", mail_data["subject"]),
                                        _email_content,
                                    )

                                post_status = account.publish_status or wp_cfg.default_status
                                wp_post = create_post(
                                    wp_cfg.site_url,
                                    wp_cfg.api_user,
                                    wp_pwd,
                                    ai_result.get("title", mail_data["subject"]),
                                    _email_content,
                                    post_status,
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
                                        status=post_status,
                                        wp_link=wp_post.get("link", ""),
                                        wordpress_settings_id=wp_cfg.id,
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

                        if published_count > 0 and account.instagram_settings_id:
                            try:
                                groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
                                if groq_cfg:
                                    img_payload_imap = None
                                    if featured_media_url := locals().get("featured_media_source_url") or ai_result.get("image_url", ""):
                                        img_payload_imap = _download_image(featured_media_url)
                                    _publish_instagram(
                                        db, ai_result, img_payload_imap,
                                        wp_image_url="",
                                        groq_key=decrypt_value(groq_cfg.encrypted_api_key),
                                        groq_model=groq_cfg.model,
                                        instagram_settings_id=account.instagram_settings_id,
                                        groq_provider=groq_cfg.provider or "groq",
                                        groq_base_url=groq_cfg.api_base_url,
                                    )
                            except Exception as ig_exc:
                                log.warning("[IG/IMAP] No se pudo publicar en Instagram: %s", ig_exc)

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


_ELEVENLABS_MAX_CHARS = 4900


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
            # ElevenLabs limita a ~5000 chars por request; truncar en oración completa
            tts_text = plain_text
            if len(tts_text) > _ELEVENLABS_MAX_CHARS:
                cut = tts_text.rfind(". ", 0, _ELEVENLABS_MAX_CHARS)
                tts_text = tts_text[: cut + 1] if cut != -1 else tts_text[:_ELEVENLABS_MAX_CHARS]
                log.info("🔊 Texto truncado para ElevenLabs: %d → %d chars", len(plain_text), len(tts_text))
            audio = el_generate(tts_text, el_key, el_cfg.voice_id, el_cfg.model_id)
            log.info("🔊 Audio ElevenLabs generado con voz '%s': %d bytes", el_cfg.voice_id, len(audio))
            return audio
    except Exception as exc:
        log.warning("ElevenLabs TTS error (voz_id=%s), intentando Edge TTS: %s", getattr(el_cfg, "voice_id", "?"), exc)

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


def _embed_html_to_wp_block(embed_html: str) -> str | None:
    """Convierte un elemento embed HTML a bloque wp:embed de Gutenberg."""
    # YouTube iframe
    yt = re.search(r'youtube(?:-nocookie)?\.com/embed/([A-Za-z0-9_-]+)', embed_html)
    if yt:
        url = f"https://www.youtube.com/watch?v={yt.group(1)}"
        return (
            f'<!-- wp:embed {{"url":"{url}","type":"video","providerNameSlug":"youtube","responsive":true}} -->\n'
            f'<figure class="wp-block-embed is-type-video is-provider-youtube wp-block-embed-youtube">'
            f'<div class="wp-block-embed__wrapper">\n{url}\n</div></figure>\n'
            f'<!-- /wp:embed -->'
        )
    # Twitter/X blockquote
    if "twitter-tweet" in embed_html:
        tw = re.search(r'https?://(?:twitter|x)\.com/\S+/status/\d+', embed_html)
        if tw:
            url = tw.group(0).rstrip('/?')
            return (
                f'<!-- wp:embed {{"url":"{url}","type":"rich","providerNameSlug":"twitter","responsive":true}} -->\n'
                f'<figure class="wp-block-embed is-type-rich is-provider-twitter wp-block-embed-twitter">'
                f'<div class="wp-block-embed__wrapper">\n{url}\n</div></figure>\n'
                f'<!-- /wp:embed -->'
            )
    # Instagram blockquote
    if "instagram-media" in embed_html:
        ig = re.search(r'https?://www\.instagram\.com/p/([A-Za-z0-9_-]+)', embed_html)
        if ig:
            url = f"https://www.instagram.com/p/{ig.group(1)}/"
            return (
                f'<!-- wp:embed {{"url":"{url}","type":"rich","providerNameSlug":"instagram","responsive":true}} -->\n'
                f'<figure class="wp-block-embed is-type-rich is-provider-instagram wp-block-embed-instagram">'
                f'<div class="wp-block-embed__wrapper">\n{url}\n</div></figure>\n'
                f'<!-- /wp:embed -->'
            )
    # Facebook plugin iframe
    if "facebook.com/plugins/" in embed_html or "facebook.com/video/embed" in embed_html:
        href = re.search(r'href=([^&"\'>\s]+)', embed_html)
        if href:
            import urllib.parse
            fb_url = urllib.parse.unquote(href.group(1))
            if fb_url.startswith("http"):
                return (
                    f'<!-- wp:embed {{"url":"{fb_url}","type":"rich","providerNameSlug":"facebook","responsive":true}} -->\n'
                    f'<figure class="wp-block-embed is-type-rich is-provider-facebook wp-block-embed-facebook">'
                    f'<div class="wp-block-embed__wrapper">\n{fb_url}\n</div></figure>\n'
                    f'<!-- /wp:embed -->'
                )
    return None


def _embeds_to_wp_blocks(embeds: list[str]) -> str:
    """Convierte una lista de embeds HTML a bloques Gutenberg concatenados."""
    blocks = [b for e in embeds if (b := _embed_html_to_wp_block(e))]
    return "\n\n".join(blocks)


def _upload_inline_images(site_url: str, api_user: str, wp_pwd: str, image_urls: list[str]) -> list[str]:
    """Descarga y sube imágenes inline a WordPress media library.
    Devuelve WP media URLs; si falla una imagen usa la URL original como fallback."""
    result = []
    for url in image_urls:
        try:
            payload = _download_image(url)
            if not payload:
                result.append(url)
                continue
            img_data, img_name, img_mime = payload
            media_result = upload_media(site_url, api_user, wp_pwd, img_data, img_name, img_mime)
            if media_result:
                _, wp_url = media_result
                log.info("  🖼 Imagen inline subida a WP: %s", wp_url[:80])
                result.append(wp_url)
            else:
                result.append(url)
        except Exception as exc:
            log.warning("No se pudo subir imagen inline %s: %s", url, exc)
            result.append(url)
    return result


def _inject_images_into_content(content: str, image_urls: list[str]) -> str:
    """Inyecta imágenes inline como bloques wp:image distribuidos entre párrafos."""
    if not image_urls or not content:
        return content

    parts = re.split(r'(</p>)', content)
    # Reconstituir párrafos completos: parte_texto + </p>
    paragraphs: list[str] = []
    i = 0
    while i < len(parts) - 1:
        paragraphs.append(parts[i] + parts[i + 1])
        i += 2
    remainder = parts[-1] if len(parts) % 2 == 1 else ""

    total = len(paragraphs)
    if total < 2:
        blocks = "".join(
            f'\n<!-- wp:image {{"sizeSlug":"large"}} -->\n'
            f'<figure class="wp-block-image size-large"><img src="{u}" alt=""/></figure>\n'
            f'<!-- /wp:image -->\n'
            for u in image_urls
        )
        return content + blocks

    step = max(1, total // (len(image_urls) + 1))
    insert_at = {step * (k + 1) for k in range(len(image_urls))}

    result: list[str] = []
    img_queue = list(image_urls)
    for idx, para in enumerate(paragraphs):
        result.append(para)
        if idx + 1 in insert_at and img_queue:
            url = img_queue.pop(0)
            result.append(
                f'\n<!-- wp:image {{"sizeSlug":"large"}} -->\n'
                f'<figure class="wp-block-image size-large"><img src="{url}" alt=""/></figure>\n'
                f'<!-- /wp:image -->\n'
            )
    return "".join(result) + remainder


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


def _publish_ai_result(db, ai_result: dict, wp_sites, image_url: str | None = None, source_name: str | None = None, inline_images: list | None = None, embeds: list | None = None, image_bytes_payload: tuple | None = None, extra_image_payloads: list | None = None, instagram_settings_id: int | None = None):
    """Publica un resultado de Groq en todos los sitios WP activos. Devuelve cantidad publicada."""
    published_count = 0

    # Descargar imagen una sola vez para todos los sitios
    # image_bytes_payload = (bytes, filename, mimetype) usado cuando ya tenemos la imagen en memoria (ej: WhatsApp)
    img_payload = None
    if image_url:
        img_payload = _download_image(image_url)
    elif image_bytes_payload:
        img_payload = image_bytes_payload

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
            media_source_url = ""
            if img_payload:
                try:
                    img_data, img_name, img_mime = img_payload
                    media_result = upload_media(
                        wp_cfg.site_url, wp_cfg.api_user, wp_pwd,
                        img_data, img_name, img_mime,
                    )
                    if media_result:
                        featured_media_id, media_source_url = media_result
                except Exception as exc:
                    log.warning("No se pudo subir imagen a %s: %s", wp_cfg.name, exc)

            # Subir imágenes inline a WP y anteponer reproductor de audio
            content = ai_result.get("content", "")
            if inline_images:
                wp_inline_imgs = _upload_inline_images(wp_cfg.site_url, wp_cfg.api_user, wp_pwd, list(inline_images))
                content = _inject_images_into_content(content, wp_inline_imgs)
            if extra_image_payloads:
                extra_wp_urls = []
                for img_b, img_n, img_m in extra_image_payloads:
                    try:
                        res = upload_media(wp_cfg.site_url, wp_cfg.api_user, wp_pwd, img_b, img_n, img_m)
                        if res:
                            _, wp_url = res
                            extra_wp_urls.append(wp_url)
                            log.info("  🖼 Imagen adicional subida a WP: %s", wp_url[:80])
                    except Exception as _exc:
                        log.warning("No se pudo subir imagen extra a %s: %s", wp_cfg.name, _exc)
                if extra_wp_urls:
                    content = _inject_images_into_content(content, extra_wp_urls)
            if embeds:
                embed_blocks = _embeds_to_wp_blocks(embeds)
                if embed_blocks:
                    content += "\n\n" + embed_blocks
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
                    wordpress_settings_id=wp_cfg.id,
                )
            )
            db.commit()
            published_count += 1

            # Difusión WhatsApp para este sitio WP (grupos asignados a él o globales)
            _broadcast_whatsapp(db, ai_result, wp_post.get("link", ""), wp_site_id=wp_cfg.id)

        except Exception as exc:
            log.error("Error publicando RSS en %s: %s", wp_cfg.name, exc)

    # Publicar en Instagram una sola vez (independiente de cuántos sitios WP haya)
    if published_count > 0:
        try:
            groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
            if groq_cfg:
                _publish_instagram(
                    db, ai_result, img_payload,
                    wp_image_url=image_url or "",
                    groq_key=decrypt_value(groq_cfg.encrypted_api_key),
                    groq_model=groq_cfg.model,
                    instagram_settings_id=instagram_settings_id,
                    groq_provider=groq_cfg.provider or "groq",
                    groq_base_url=groq_cfg.api_base_url,
                )
        except Exception as exc:
            log.warning("[IG] No se pudo publicar en Instagram: %s", exc)

    return published_count


def _publish_instagram(db, ai_result: dict, img_payload: tuple | None, wp_image_url: str, groq_key: str, groq_model: str, instagram_settings_id: int | None = None, groq_provider: str = "groq", groq_base_url: str | None = None):
    """Publica en Instagram usando la cuenta vinculada al feed/IMAP (o la primera activa como fallback)."""
    try:
        from app.services.image_template_service import build_instagram_image
        from app.services.instagram_service import publish_image

        if instagram_settings_id:
            ig = db.query(InstagramSettings).filter(
                InstagramSettings.id == instagram_settings_id,
                InstagramSettings.is_active == True,
            ).first()
        else:
            ig = db.query(InstagramSettings).filter(InstagramSettings.is_active == True).first()

        if not ig or not ig.ig_user_id or not ig.encrypted_access_token:
            return

        # Verificar límite diario usando logs de Instagram
        from datetime import date, datetime, timezone as tz
        today_start = datetime.now(tz.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = db.query(Log).filter(
            Log.source == "instagram",
            Log.level == "INFO",
            Log.created_at >= today_start,
        ).count()
        if today_count >= ig.max_posts_per_day:
            log.info("[IG] Límite diario alcanzado (%d/%d)", today_count, ig.max_posts_per_day)
            return

        # Necesitamos bytes de imagen para procesar con Pillow
        if img_payload:
            img_data, _, _ = img_payload
        elif wp_image_url:
            downloaded = _download_image(wp_image_url)
            if not downloaded:
                log.warning("[IG] No se pudo descargar imagen para Instagram")
                return
            img_data, _, _ = downloaded
        else:
            log.info("[IG] Sin imagen disponible — saltando publicación IG")
            return

        title = ai_result.get("title", "")

        ig_image_bytes = build_instagram_image(
            img_data,
            title,
            logo_path=ig.logo_path,
            logo_position=ig.logo_position or "bottom-right",
            logo_size=ig.logo_size or 180,
            gradient_color=ig.gradient_color or "#000000",
            gradient_opacity=ig.gradient_opacity or 200,
            gradient_height=ig.gradient_height or 480,
            font_size=ig.font_size or 62,
            text_color=ig.text_color or "#ffffff",
            banner_text=ig.banner_text or None,
            banner_color=ig.banner_color or "#e53935",
            banner_text_color=ig.banner_text_color or "#ffffff",
            text_align=ig.text_align or "left",
            title_y_offset=ig.title_y_offset or 0,
            font_family=ig.font_family or "sans",
            text_bg_color=ig.text_bg_color or "#000000",
            text_bg_opacity=ig.text_bg_opacity or 0,
            font_weight=ig.font_weight or "bold",
            banner_style=ig.banner_style or "pill",
            banner_font_weight=ig.banner_font_weight or "bold",
            banner_y_offset=ig.banner_y_offset or 0,
            banner_align=ig.banner_align or "center",
            text_bg_padding_x=ig.text_bg_padding_x or 0,
            text_bg_padding_y=ig.text_bg_padding_y if ig.text_bg_padding_y is not None else 18,
            text_bg_full_width=ig.text_bg_full_width if ig.text_bg_full_width is not None else True,
            title_max_lines=ig.title_max_lines or 4,
            category=ai_result.get("category", "") if ig.show_category else None,
            show_category=bool(ig.show_category),
            category_bg_color=ig.category_bg_color or "#e53935",
            category_text_color=ig.category_text_color or "#ffffff",
            category_position=ig.category_position or "top-left",
        )

        # Subir la imagen procesada al primer sitio WP activo para obtener URL pública
        wp = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).first()
        if not wp:
            log.warning("[IG] No hay sitio WordPress activo para subir imagen de Instagram")
            return

        # Generar caption con Groq (después de tener wp.site_url disponible)
        website_footer = ig.banner_text or wp.site_url or ""
        caption = _generate_ig_caption(groq_key, groq_model, title, ai_result.get("summary", ""), website_footer, groq_provider=groq_provider, groq_base_url=groq_base_url)

        wp_pwd = decrypt_value(wp.encrypted_app_password)
        media_result = upload_media(
            wp.site_url, wp.api_user, wp_pwd,
            ig_image_bytes, "instagram_post.jpg", "image/jpeg",
        )
        if not media_result:
            log.warning("[IG] No se pudo subir imagen de Instagram a WordPress")
            return

        _, public_url = media_result
        token = decrypt_value(ig.encrypted_access_token)
        result = publish_image(ig.ig_user_id, token, public_url, caption)

        if result["ok"]:
            log.info("  📸 [IG] Publicado en Instagram: %s", title[:80])
            _log_db(db, "INFO", f"[Instagram] Publicado: {title[:120]}", source="instagram")
        else:
            log.warning("  ⚠️ [IG] Error publicando en Instagram: %s", result["error"])
            _log_db(db, "WARN", f"[Instagram] Error: {result['error']}", source="instagram")

    except Exception as exc:
        log.error("[IG] Excepción en _publish_instagram: %s", exc)


def _generate_ig_caption(groq_key: str, groq_model: str, title: str, summary: str, website_footer: str = "", groq_provider: str = "groq", groq_base_url: str | None = None) -> str:
    """Genera caption Instagram: frase gancho + copy con emojis + 5 hashtags virales + footer web."""
    footer = f"\n\n📰 {website_footer}" if website_footer else ""
    try:
        from app.services.groq_service import _get_client
        client = _get_client(groq_key, provider=groq_provider, api_base_url=groq_base_url)
        prompt = (
            "Sos community manager de un medio digital. El título de la noticia ya está en la imagen, "
            "NO lo repitas en el caption. Generá un caption para Instagram con exactamente esta estructura:\n"
            "1. Una frase gancho atractiva e impactante (máx. 2 líneas, con emojis)\n"
            "2. Un copy breve y directo con emojis integrados (2-3 líneas)\n"
            "3. Exactamente 5 hashtags virales y de alto alcance en español\n"
            "Máximo 220 palabras en total. Solo devolvé el caption listo para publicar, sin comentarios.\n\n"
            f"Noticia — Título: {title}\nResumen: {summary}"
        )
        resp = client.chat.completions.create(
            model=groq_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=350,
        )
        text = resp.choices[0].message.content.strip()
        return text + footer
    except Exception:
        return f"🔥 ¡No te pierdas esta noticia! 👇\n\n#noticias #argentina #informacion #actualidad #hoy{footer}"


def _broadcast_whatsapp(db, ai_result: dict, wp_url: str, wp_site_id: int | None = None):
    """Envía el artículo recién publicado a los grupos de WhatsApp de cada cuenta activa."""
    try:
        from app.models import WhatsAppSettings, WhatsAppGroup
        from app.services.whatsapp_service import send_text

        wa_accounts = db.query(WhatsAppSettings).filter(
            WhatsAppSettings.broadcast_enabled == True,
        ).all()
        if not wa_accounts:
            return

        title = ai_result.get("title", "")
        summary = ai_result.get("summary", "")

        for s in wa_accounts:
            if not s.evolution_api_url or not s.evolution_api_key:
                continue
            # Filtrar por WP site: cuenta asignada a un WP específico que no es el que publicó
            if s.wordpress_settings_id is not None and wp_site_id is not None:
                if s.wordpress_settings_id != wp_site_id:
                    continue

            all_groups = db.query(WhatsAppGroup).filter(
                WhatsAppGroup.enabled == True,
                WhatsAppGroup.whatsapp_settings_id == s.id,
            ).all()
            if not all_groups:
                continue

            if wp_site_id is not None:
                groups = [g for g in all_groups
                          if g.wordpress_settings_id is None or g.wordpress_settings_id == wp_site_id]
            else:
                groups = all_groups

            if not groups:
                continue

            template = s.broadcast_template or "*{title}*\n\n{summary}\n\n{url}"
            text = template.replace("{title}", title).replace("{summary}", summary).replace("{url}", wp_url)

            for g in groups:
                send_text(s.evolution_api_url, s.evolution_api_key, s.instance_name, g.jid, text)
                log.info("WA difusión → %s (%s) vía %s", g.name, g.jid, s.name)
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

                log.info("📡 Revisando feed: %s (tipo=%s)", feed.name, feed.feed_type or "rss")
                if (feed.feed_type or "rss") == "web":
                    items = scrape_category_page(feed.url)
                else:
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
                        inline_images: list[str] = []
                        embeds: list[str] = []

                        # Si el RSS solo trae un excerpt corto o contenido binario, scrapear el artículo completo
                        if item.get("needs_scraping") and item["link"]:
                            log.info("  🔍 Scrapeando artículo completo: %s", item["link"][:80])
                            scraped_text, scraped_img, inline_images, embeds = scrape_full_article(item["link"])
                            if scraped_text:
                                body = scraped_text
                                # Re-verificar filtro con el texto completo del artículo
                                if not _matches_keyword_filter(feed.keyword_filter, item["title"], body):
                                    log.info("  ⏭ Descartado por filtro (cuerpo): %s", item["title"][:80])
                                    rss_item.status = "skipped"
                                    db.commit()
                                    continue
                            elif not body:
                                # Ni el RSS ni el scraping tienen contenido legible (ej: PDF embebido)
                                log.warning("  ⏭ Sin contenido legible (PDF/binario): %s", item["title"][:80])
                                _log_db(db, "WARN", f"[RSS] {feed.name}: sin contenido legible — {item['title'][:120]}", source="rss")
                                rss_item.status = "skipped"
                                db.commit()
                                continue
                            else:
                                # Scraping falló o fue garbled pero el RSS tiene un excerpt legible
                                log.info("  📋 Usando descripción del RSS (scraping no disponible): %s", item["title"][:60])
                            # og:image del artículo siempre es mejor que el thumbnail del RSS
                            if scraped_img:
                                image_url = scraped_img
                        elif item["link"]:
                            # Aunque el contenido sea completo, la og:image del artículo
                            # siempre es de mayor resolución que el thumbnail del feed RSS
                            _, scraped_img, inline_images, embeds = scrape_full_article(item["link"])
                            if scraped_img:
                                image_url = scraped_img
                                log.info("  🖼 og:image scrapeada: %s", scraped_img[:80])

                        # Última línea de defensa: si el body sigue siendo binario/ilegible, omitir
                        if _is_garbled(body):
                            log.warning("  ⏭ Body garbled tras scraping, omitiendo: %s", item["title"][:80])
                            _log_db(db, "WARN", f"[RSS] {feed.name}: contenido binario/ilegible — {item['title'][:120]}", source="rss")
                            rss_item.status = "skipped"
                            db.commit()
                            continue

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

                        # ── Verificación de calidad automática ───────────────
                        quality_ok, quality_reason = _check_content_quality(ai_result, len(body))
                        if not quality_ok:
                            warn_msg = f"[RSS] {feed.name}: calidad insuficiente — {quality_reason} — '{item['title'][:80]}'"
                            log.warning("  ⚠ %s", warn_msg)
                            _log_db(db, "WARN", warn_msg, source="rss")
                            rss_item.status = "error"
                            rss_item.error_message = f"Calidad: {quality_reason}"
                            db.commit()
                            continue
                        log.info("  ✔ Calidad: %s", quality_reason)

                        # Si no hay imagen, generar una con IA relacionada al tema
                        if not image_url:
                            image_url = _generate_fallback_image(
                                ai_result.get("title", item["title"]),
                                ai_result.get("category", ""),
                            )

                        rss_item.status = "processed"
                        db.commit()

                        # Publicar solo en los sitios asignados al feed (o todos si no tiene asignación)
                        feed_wp_sites = (
                            [s for s in wp_sites if s.id in json.loads(feed.wp_site_ids)]
                            if feed.wp_site_ids
                            else wp_sites
                        )
                        count = _publish_ai_result(db, ai_result, feed_wp_sites, image_url=image_url, source_name=feed.name, inline_images=inline_images, embeds=embeds, instagram_settings_id=feed.instagram_settings_id)
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


def generate_rss_preview(db, feed: RssFeed, item: dict) -> dict:
    """Genera el contenido AI sin publicar a WordPress. Para vista previa antes de publicar."""
    groq_cfg = db.query(GroqSettings).filter(GroqSettings.is_active == True).first()
    wp_sites = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).all()
    if not groq_cfg:
        raise ValueError("Groq no configurado — actívalo en Configuración → Groq IA")

    groq_key = decrypt_value(groq_cfg.encrypted_api_key)
    wp_categories = _fetch_wp_category_names(wp_sites)

    body = item["body"]
    image_url = item.get("image_url")
    inline_images: list[str] = []
    embeds: list[str] = []

    if item.get("needs_scraping") and item["link"]:
        log.info("[preview] Scrapeando artículo: %s", item["link"][:80])
        scraped_text, scraped_img, inline_images, embeds = scrape_full_article(item["link"])
        if scraped_text:
            body = scraped_text
        if scraped_img:
            image_url = scraped_img
    elif item["link"]:
        _, scraped_img, inline_images, embeds = scrape_full_article(item["link"])
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

    return {
        "title": ai_result.get("title", item["title"]),
        "content": ai_result.get("content", ""),
        "summary": ai_result.get("summary", ""),
        "category": ai_result.get("category", ""),
        "tags": ai_result.get("tags", []),
        "image_url": image_url or "",
        "_ai_result": ai_result,
        "_inline_images": inline_images,
        "_embeds": embeds,
    }


def confirm_publish_rss_item(db, feed: RssFeed, cached: dict) -> dict:
    """Publica a WordPress usando el contenido ya generado en generate_rss_preview."""
    wp_sites = db.query(WordPressSettings).filter(WordPressSettings.is_active == True).all()
    if not wp_sites:
        raise ValueError("WordPress no configurado — agrega un sitio en Configuración → WordPress")

    ai_result = cached["ai_result"]
    image_url = cached["image_url"]
    inline_images = cached.get("inline_images", [])
    embeds = cached.get("embeds", [])
    item_data = cached["item"]

    rss_item = db.query(ProcessedRssItem).filter(ProcessedRssItem.guid == item_data["guid"]).first()
    if not rss_item:
        rss_item = ProcessedRssItem(
            rss_feed_id=feed.id,
            guid=item_data["guid"],
            title=item_data["title"],
            link=item_data["link"],
            published_at=item_data.get("published_at"),
            status="received",
        )
        db.add(rss_item)
        db.commit()
        db.refresh(rss_item)

    feed_wp_sites = (
        [s for s in wp_sites if s.id in json.loads(feed.wp_site_ids)]
        if feed.wp_site_ids
        else wp_sites
    )
    count = _publish_ai_result(
        db, ai_result, feed_wp_sites,
        image_url=image_url, source_name=feed.name,
        inline_images=inline_images, embeds=embeds,
        instagram_settings_id=feed.instagram_settings_id,
    )

    if count > 0:
        rss_item.status = "published"
        db.commit()
        post = db.query(Post).filter(Post.source_name == feed.name).order_by(Post.id.desc()).first()
        return {
            "title": ai_result.get("title", item_data["title"]),
            "wp_link": post.wp_link if post else "",
        }

    rss_item.status = "error"
    db.commit()
    raise ValueError("No se pudo publicar en ningún sitio WordPress")


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
    inline_images: list[str] = []
    embeds: list[str] = []

    if item.get("needs_scraping") and item["link"]:
        log.info("[manual] Scrapeando artículo: %s", item["link"][:80])
        scraped_text, scraped_img, inline_images, embeds = scrape_full_article(item["link"])
        if scraped_text:
            body = scraped_text
        if scraped_img:
            image_url = scraped_img
    elif item["link"]:
        _, scraped_img, inline_images, embeds = scrape_full_article(item["link"])
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

    feed_wp_sites = (
        [s for s in wp_sites if s.id in json.loads(feed.wp_site_ids)]
        if feed.wp_site_ids
        else wp_sites
    )
    count = _publish_ai_result(db, ai_result, feed_wp_sites, image_url=image_url, source_name=feed.name, inline_images=inline_images, embeds=embeds, instagram_settings_id=feed.instagram_settings_id)

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
