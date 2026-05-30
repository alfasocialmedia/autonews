from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone

import feedparser
import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("rss_service")

_SCRAPE_TIMEOUT = 15
_MIN_BODY_LENGTH = 350

# Selectores de elementos a eliminar (publicidad, nav, social, etc.)
_NOISE_TAGS = [
    "script", "style", "nav", "header", "footer", "aside",
    "form", "iframe", "noscript", "figure",
]
_NOISE_SELECTORS = [
    "[class*='ad-']", "[class*='-ad']", "[id*='-ad']", "[id*='ad-']",
    "[class*='banner']", "[class*='sidebar']", "[class*='social']",
    "[class*='share']", "[class*='related']", "[class*='comment']",
    "[class*='subscribe']", "[class*='newsletter']", "[class*='promo']",
    "[class*='publicidad']", "[class*='propaganda']",
    "[class*='widget']", "[class*='popup']", "[class*='modal']",
    "[class*='cookie']", "[class*='overlay']",
    # Reproductores de audio/video (MediaElement.js, WordPress audio shortcode)
    "[class*='mejs-container']", "[class*='mejs-inner']",
    "[class*='wp-audio-shortcode']", "[class*='wp-video-shortcode']",
    # Bloques de publicidad inyectada (plugins Code Block, Ad Inserter, Tagdiv)
    "[class*='code-block']", "[class*='ai-viewport']",
    "[class*='td-a-rec']", "[class*='tdi_']",
    # Reproductores externos y embeds no deseados en el texto
    "[class*='aniview']", "[class*='video-container']",
]


_MIN_IMAGE_WIDTH = 400  # px mínimos para aceptar una imagen del feed
_GARBLED_THRESHOLD = 0.04  # >4 % de chars no imprimibles → contenido binario/PDF


def _is_garbled(text: str) -> bool:
    """True si el texto contiene datos binarios/PDF mal decodificados.

    Detecta:
    - Caracteres de control (salvo \\n \\r \\t)
    - Símbolos Unicode geométricos/misceláneos (◆ □ ◘ etc.) — categorías So/Sm/Sk/Cs
    - Carácter de reemplazo U+FFFD
    - Bloque Geometric Shapes (U+25A0–U+25FF) y Misc Symbols (U+2600–U+26FF)
    """
    if not text or len(text) < 80:
        return False
    bad = 0
    for c in text:
        code = ord(c)
        cat = unicodedata.category(c)
        if cat == "Cc" and c not in "\n\r\t":          # control sin whitespace
            bad += 1
        elif cat in ("So", "Sm", "Sk", "Cs"):          # símbolos y surrogates
            bad += 1
        elif code == 0xFFFD:                            # replacement character
            bad += 1
        elif 0x25A0 <= code <= 0x25FF:                  # Geometric Shapes block
            bad += 1
        elif 0x2600 <= code <= 0x27FF:                  # Misc Symbols / Dingbats
            bad += 1
    return (bad / len(text)) > _GARBLED_THRESHOLD


def _upgrade_wp_thumbnail(url: str) -> str:
    """Convierte thumbnail WordPress (imagen-300x168.jpg) a la imagen original sin sufijo de tamaño."""
    return re.sub(r'-\d+x\d+(\.\w+(?:\?.*)?$)', r'\1', url)


def _extract_image_url(entry) -> str | None:
    # media_content: aceptar solo si no declara dimensiones o supera el mínimo
    for mc in entry.get("media_content", []):
        url = mc.get("url", "")
        if not url:
            continue
        if not (mc.get("type", "").startswith("image/") or re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, re.I)):
            continue
        width = int(mc.get("width") or 0)
        if width == 0 or width >= _MIN_IMAGE_WIDTH:
            return _upgrade_wp_thumbnail(url)

    # media_thumbnail omitido: son miniaturas pequeñas; se prefiere og:image al scrapear

    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image/"):
            url = enc.get("href") or enc.get("url") or ""
            return _upgrade_wp_thumbnail(url) if url else None

    html = ""
    if entry.get("content"):
        html = entry.content[0].get("value", "")
    if not html:
        html = entry.get("summary", "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    if m:
        return _upgrade_wp_thumbnail(m.group(1))

    return None


def _is_comment_entry(entry) -> bool:
    """Detecta entradas de comentarios en feeds RSS (WordPress, Disqus, etc.)."""
    for field in ("id", "link"):
        url = entry.get(field, "")
        if re.search(r'[#?&/]comment|replytocom|#respond', url, re.I):
            return True
    return entry.get("type") == "comment"


def _extract_og_image(soup: BeautifulSoup) -> str | None:
    # og:image / twitter:image (case-insensitive via BeautifulSoup string search)
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("og:image", "twitter:image", "og:image:url"):
            val = tag.get("content") or tag.get("value") or ""
            if val and val.startswith("http"):
                return val
    # Fallback: primera imagen grande en el cuerpo del artículo
    article = soup.find("article") or soup.find("main")
    if article:
        for img in article.find_all("img", src=True):
            src = img["src"]
            if src.startswith("http") and not any(x in src for x in ("logo", "icon", "avatar", "pixel", "tracking")):
                return src
    return None


_IMG_SKIP = ("logo", "icon", "avatar", "pixel", "tracking", "spinner", "btn", "arrow", "spacer", "badge", "placeholder")
_LAZY_ATTRS = ("data-src", "data-lazy-src", "data-original", "data-lazy", "data-lazyload", "data-full-url", "data-td-src-property")

# Filtros para imágenes promocionales (banners sociales, newsletters, etc.)
_PROMO_ALT_RE = re.compile(
    r"sumate|canal\s+de\s+whatsapp|canal\s+de\s+telegram|seguinos|nuestro\s+canal|"
    r"grupo\s+de\s+whatsapp|grupo\s+de\s+telegram|compartir|publicidad|propaganda",
    re.I,
)
_PROMO_HREF_RE = re.compile(r"whatsapp\.com|t\.me/|telegram\.me|bit\.ly|linktr\.ee", re.I)
_PROMO_CONTAINER_RE = re.compile(r"whatsapp|telegram|promo|banner|publicidad|social[-_]?link|compartir|newsletter|suscri", re.I)


def _extract_inline_images(container, og_image: str | None) -> list[str]:
    """Extrae URLs de imágenes editoriales dentro del artículo (hasta 2, excluye la og:image).
    Maneja lazy loading (data-src, data-lazy-src, data-original, etc.)."""
    seen: set[str] = {og_image} if og_image else set()
    images: list[str] = []
    for img in container.find_all("img"):
        # Resolver URL real: priorizar src; si es placeholder/data-URI buscar atributos lazy
        src = img.get("src", "").strip()
        if not src or src.startswith("data:") or not src.startswith("http"):
            for attr in _LAZY_ATTRS:
                val = img.get(attr, "").strip()
                if val and val.startswith("http"):
                    src = val
                    break

        if not src or not src.startswith("http"):
            continue

        src = _upgrade_wp_thumbnail(src)

        if src in seen:
            continue
        if any(x in src.lower() for x in _IMG_SKIP):
            continue

        # Saltar imágenes promocionales por alt/title
        alt = (img.get("alt") or img.get("title") or "").strip()
        if _PROMO_ALT_RE.search(alt):
            continue
        # Saltar si la imagen está dentro de un enlace a red social/promo
        parent_a = img.find_parent("a")
        if parent_a and _PROMO_HREF_RE.search(parent_a.get("href", "")):
            continue
        # Saltar si algún contenedor padre tiene clases promocionales
        promo_parent = False
        for p in img.parents:
            if not hasattr(p, "get") or p.name in (None, "[document]", "html", "body"):
                break
            if _PROMO_CONTAINER_RE.search(" ".join(p.get("class", []))):
                promo_parent = True
                break
        if promo_parent:
            continue

        try:
            w = int(img.get("width") or 0)
            h = int(img.get("height") or 0)
            if w and w < 250:
                continue
            if h and h < 150:
                continue
        except (ValueError, TypeError):
            pass
        seen.add(src)
        images.append(src)
        if len(images) >= 2:  # máximo 2 imágenes inline por artículo
            break
    return images


def _extract_social_embeds(container) -> list[str]:
    """Extrae iframes de YouTube/Facebook y blockquotes de Twitter/Instagram del artículo."""
    embeds: list[str] = []
    for iframe in container.find_all("iframe", src=True):
        src = iframe.get("src", "")
        if "youtube.com/embed/" in src or "youtube-nocookie.com/embed/" in src:
            embeds.append(str(iframe))
        elif "facebook.com/plugins/" in src or "facebook.com/video/embed" in src:
            embeds.append(str(iframe))
    for bq in container.find_all("blockquote", class_=True):
        classes = " ".join(bq.get("class", []))
        if "twitter-tweet" in classes or "instagram-media" in classes:
            embeds.append(str(bq))
    return embeds


_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}

_RSS_HEADERS = {
    **_SCRAPE_HEADERS,
    "Accept": "application/rss+xml,application/atom+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.7",
}

_JSONLD_ARTICLE_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting"}


def _extract_jsonld_text(soup: BeautifulSoup) -> str:
    """Extrae articleBody de datos estructurados JSON-LD (schema.org).
    La mayoría de los grandes medios (La Nacion, Infobae, Clarín) incluyen
    el artículo completo aquí, incluso cuando el HTML está parcialmente oculto por paywall o JS.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or ""
            data = json.loads(raw)
            # Puede ser objeto único o lista [@graph o array]
            candidates = data if isinstance(data, list) else [data]
            # Buscar también en @graph
            if isinstance(data, dict) and "@graph" in data:
                candidates = data["@graph"]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                if item.get("@type") in _JSONLD_ARTICLE_TYPES:
                    body = item.get("articleBody", "")
                    if len(body) > 300:
                        return body
        except Exception:
            continue
    return ""


def _find_article_body(soup: BeautifulSoup):
    """Encuentra el contenedor del cuerpo del artículo con selectores progresivos."""
    # itemprop es el más preciso
    el = soup.find(attrs={"itemprop": "articleBody"})
    if el:
        return el

    # Buscar dentro de <article> primero (evita capturar nav/sidebar)
    article_tag = soup.find("article")
    search_in = article_tag if article_tag else soup

    # Clases comunes de temas WordPress y otros CMS de noticias (incl. Crónica, Misiones Online, etc.)
    _BODY_CLASSES = re.compile(
        r"entry[-_]content|post[-_]content|article[-_]content|article[-_]body|"
        r"post[-_]body|content[-_]body|story[-_]body|article__body|td-post[-_]content|"
        r"td_block_wrap|tdb-block-inner|tdb_single_content|tdb-single-content|"
        r"mvp-content-main|jeg_content|"
        r"single[-_]content|nota[-_]cuerpo|cuerpo[-_]nota|news[-_]content|"
        # Crónica y sitios argentinos
        r"cronica[-_]content|nota[-_]content|contenido[-_]nota|cuerpo[-_]articulo|"
        r"article[-_]text|nota[-_]texto|texto[-_]nota|bajada|volanta|"
        # Genérico ampliado
        r"article__content|article__body|articulo[-_]cuerpo|post__content|"
        r"single__content|main[-_]content|page[-_]content|richtext",
        re.I,
    )
    el = search_in.find(class_=_BODY_CLASSES)
    if el:
        return el

    # Si encontramos <article> úsalo aunque no tenga clase específica
    if article_tag:
        return article_tag

    # Último recurso: main o div/section con clase/id genérico
    return (
        soup.find("main")
        or soup.find(["div", "section"], class_=re.compile(r"(content|nota|cuerpo|story|news|article|texto|articulo)", re.I))
        or soup.find(["div", "section"], id=re.compile(r"(content|nota|cuerpo|story|news|article|texto|articulo)", re.I))
    )


def _extract_first_figure_image(container) -> str | None:
    """Primera imagen editorial dentro de un <figure> en el artículo.
    Se prefiere sobre og:image porque la og:image suele tener marca de agua del sitio fuente.
    Saltea figures que contienen video/iframe (thumbnail del reproductor, no imagen editorial)."""
    if not container:
        return None
    for fig in container.find_all("figure"):
        # Ignorar figures de reproductores de video/audio/embed
        if fig.find(["video", "iframe", "audio", "embed"]):
            continue
        img = fig.find("img")
        if not img:
            continue
        src = img.get("src", "").strip()
        if not src or src.startswith("data:") or not src.startswith("http"):
            for attr in _LAZY_ATTRS:
                val = img.get(attr, "").strip()
                if val and val.startswith("http"):
                    src = val
                    break
        if not src or not src.startswith("http"):
            continue
        if any(x in src.lower() for x in _IMG_SKIP):
            continue
        alt = (img.get("alt") or "").strip()
        if _PROMO_ALT_RE.search(alt):
            continue
        upgraded = _upgrade_wp_thumbnail(src)
        # Si la URL aún contiene indicador de tamaño pequeño (ej: ?w=120, /120x90/), continuar
        if re.search(r'[?&/](?:w|width)=\d{1,3}(?:[^0-9]|$)', upgraded, re.I):
            continue
        return upgraded
    return None


def scrape_full_article(url: str) -> tuple[str, str | None, list[str], list[str]]:
    """Extrae texto, imagen principal, imágenes inline y embeds de un artículo.
    Extrae SIEMPRE por JSON-LD y por HTML, y usa el resultado más largo.
    Usa cloudscraper para sitios con Cloudflare, httpx como fallback.
    """
    content = _fetch_html(url)
    if not content:
        log.warning("No se pudo scrapear %s", url)
        return "", None, [], []

    soup = BeautifulSoup(content, "html.parser")
    og_image = _extract_og_image(soup)

    # Extraer multimedia ANTES de eliminar ruido (iframes y figures se pierden después)
    pre_article = _find_article_body(soup)
    pre_container = pre_article if pre_article else soup

    # Preferir imagen editorial del cuerpo sobre og:image (og:image suele tener marca de agua)
    figure_img = _extract_first_figure_image(pre_container)
    primary_image = figure_img or og_image

    inline_images = _extract_inline_images(pre_container, primary_image)
    embeds = _extract_social_embeds(pre_container)

    # ── Candidato 1: JSON-LD ─────────────────────────────────────────────────
    # Los temas WordPress (Newspaper/Tagdiv, La Nacion, Infobae) suelen incluir
    # articleBody completo en JSON-LD. Pero algunos temas solo ponen el extracto.
    jsonld_candidate = ""
    jsonld_text = _extract_jsonld_text(soup)
    if jsonld_text:
        lines = [l.strip() for l in jsonld_text.splitlines() if l.strip()]
        cand = "\n".join(lines)[:12000]
        if not _is_garbled(cand):
            jsonld_candidate = cand
            log.info("JSON-LD candidate: %d chars", len(jsonld_candidate))
        else:
            log.warning("scrape: JSON-LD garbled: %s", url)

    # ── Candidato 2: scraping HTML ───────────────────────────────────────────
    # Eliminar ruido de la soup (scripts, ads, reproductores, nav, etc.)
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    for sel in _NOISE_SELECTORS:
        try:
            for el in soup.select(sel):
                el.decompose()
        except Exception:
            pass
    for a in soup.find_all("a"):
        if len((a.get_text() or "").strip()) < 4:
            a.decompose()

    article = _find_article_body(soup)
    container = article if article else soup

    def _extract_paras(el) -> list[str]:
        return [p.get_text(strip=True) for p in el.find_all("p") if p.get_text(strip=True)]

    paras = _extract_paras(container)
    if paras:
        html_candidate = "\n\n".join(paras)[:12000]
    else:
        text = container.get_text(separator="\n", strip=True)
        lines2 = [l.strip() for l in text.splitlines() if l.strip()]
        html_candidate = "\n".join(lines2)[:12000]

    # Si el candidato HTML es corto, intentar combinar múltiples contenedores del mismo tipo.
    # Algunos sitios (C5N, etc.) parten el artículo en varios elementos article-body.
    if len(html_candidate) < 3000:
        _MULTI_BODY = re.compile(
            r"article[-_]body|entry[-_]content|post[-_]content|article[-_]content|"
            r"story[-_]body|article__body|td-post[-_]content|tdb-single-content|"
            r"note[-_]content|content[-_]article",
            re.I,
        )
        all_bodies = soup.find_all(class_=_MULTI_BODY)
        if len(all_bodies) > 1:
            combined: list[str] = []
            seen_p: set[str] = set()
            for body_el in all_bodies:
                for p_text in _extract_paras(body_el):
                    if p_text not in seen_p:
                        seen_p.add(p_text)
                        combined.append(p_text)
            combined_text = "\n\n".join(combined)[:12000]
            if len(combined_text) > len(html_candidate):
                html_candidate = combined_text
                log.info("HTML multi-body: %d contenedores → %d chars", len(all_bodies), len(html_candidate))

    if _is_garbled(html_candidate):
        log.warning("scrape: HTML garbled: %s", url)
        html_candidate = ""
    elif html_candidate:
        log.info("HTML candidate: %d chars, container=%s",
                 len(html_candidate), container.name if article else "body")

    # ── Elegir el candidato más completo ────────────────────────────────────
    # Usar el más largo: JSON-LD puede ser solo el extracto (corto),
    # mientras que el HTML puede tener el artículo completo.
    if jsonld_candidate and html_candidate:
        if len(jsonld_candidate) >= len(html_candidate):
            result = jsonld_candidate
            log.info("scrape: JSON-LD ganó (%d > %d chars)", len(jsonld_candidate), len(html_candidate))
        else:
            result = html_candidate
            log.info("scrape: HTML ganó (%d > %d chars)", len(html_candidate), len(jsonld_candidate))
    elif jsonld_candidate:
        result = jsonld_candidate
        log.info("scrape: solo JSON-LD (%d chars)", len(jsonld_candidate))
    elif html_candidate:
        result = html_candidate
        log.info("scrape: solo HTML (%d chars)", len(html_candidate))
    else:
        log.warning("scrape: sin contenido legible: %s", url)
        return "", primary_image, inline_images, embeds

    log.info("scrape ok: %d chars total, primary_image=%s (figure=%s), images=%d, embeds=%d",
             len(result), bool(primary_image), bool(figure_img), len(inline_images), len(embeds))
    log.debug("scrape preview: %s", result[:300])
    return result, primary_image, inline_images, embeds


def _parse_feed_bytes(content: bytes, text: str):
    """Intenta parsear feed con varias estrategias de encoding."""
    # Eliminar BOM UTF-8 si está presente
    raw = content.lstrip(b"\xef\xbb\xbf")

    feed = feedparser.parse(raw)
    if feed.entries:
        return feed

    # Intentar con texto ya decodificado por httpx
    feed2 = feedparser.parse(text)
    if feed2.entries:
        return feed2

    # Devolver el primero aunque esté vacío (el caller decide)
    return feed


def _download_feed(feed_url: str):
    """Descarga y parsea un feed RSS con múltiples estrategias ante bloqueos."""
    last_exc: Exception | None = None
    try:
        resp = httpx.get(
            feed_url,
            timeout=15,
            follow_redirects=True,
            headers=_RSS_HEADERS,
            verify=False,
        )
        if resp.status_code != 403:
            resp.raise_for_status()
            feed = _parse_feed_bytes(resp.content, resp.text)
            if feed.entries:
                return feed
            # Guardar para posible re-raise si todos los fallbacks fallan
            if feed.bozo:
                last_exc = feed.bozo_exception
    except httpx.HTTPStatusError:
        pass
    except Exception as exc:
        last_exc = exc

    # Fallback 1: feedparser descarga directamente con su propio User-Agent
    feed = feedparser.parse(feed_url)
    if feed.entries:
        return feed

    # Fallback 2: httpx sin headers especiales
    try:
        resp2 = httpx.get(feed_url, timeout=15, follow_redirects=True, verify=False)
        resp2.raise_for_status()
        feed2 = _parse_feed_bytes(resp2.content, resp2.text)
        if feed2.entries:
            return feed2
    except Exception:
        pass

    if last_exc:
        raise ValueError(f"Feed inválido o inaccesible: {last_exc}") from last_exc
    raise ValueError(f"No se pudo descargar el feed: {feed_url}")


def fetch_rss_items(feed_url: str) -> list[dict]:
    """Descarga y parsea un feed RSS. Devuelve lista de items normalizados."""
    # Descargar con httpx (timeout, SSL, redirects) y luego parsear el contenido.
    # feedparser.parse(url) usa urllib sin timeout y puede fallar con certificados en VPS.
    feed = _download_feed(feed_url)

    if feed.bozo and not feed.entries:
        raise ValueError(f"Feed inválido o sin artículos: {feed.bozo_exception}")

    items = []
    for entry in feed.entries:
        if _is_comment_entry(entry):
            continue

        guid = entry.get("id") or entry.get("link") or ""
        if not guid:
            continue

        content = ""
        if entry.get("content"):
            content = entry.content[0].get("value", "")
        if not content:
            content = entry.get("summary", "")

        published_at = None
        if entry.get("published_parsed"):
            try:
                published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass

        # Si el cuerpo del RSS trae datos binarios/PDF, limpiarlo y forzar scraping
        body_garbled = _is_garbled(content)
        if body_garbled:
            log.info("RSS body binario/ilegible en '%s' — se forzará scraping", entry.get("title", "")[:60])
            content = ""

        items.append({
            "guid": guid,
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", ""),
            "body": content,
            "published_at": published_at,
            "image_url": _extract_image_url(entry),
            "needs_scraping": len(content) < _MIN_BODY_LENGTH or body_garbled,
        })

    return items


def test_rss_feed(url: str) -> tuple[bool, str]:
    try:
        items = fetch_rss_items(url)
        if not items:
            return False, "El feed no contiene ítems o no es accesible."
        return True, f"Feed válido — {len(items)} artículos encontrados."
    except Exception as exc:
        return False, str(exc)


# ── Scraper de páginas de categoría (fuentes web sin RSS) ─────────────────────

_WEB_TIMEOUT = 20


def _fetch_html(url: str) -> bytes | None:
    """Descarga HTML usando cloudscraper (Cloudflare) o httpx como fallback."""
    # 1. Intentar con cloudscraper (maneja desafíos JS de Cloudflare)
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
        resp = scraper.get(url, timeout=_WEB_TIMEOUT)
        if resp.status_code == 200 and len(resp.content) > 500:
            log.info("_fetch_html cloudscraper ok: %d bytes", len(resp.content))
            return resp.content
    except Exception as exc:
        log.debug("cloudscraper falló, intentando httpx: %s", exc)

    # 2. Fallback con httpx
    try:
        resp = httpx.get(url, timeout=_WEB_TIMEOUT, follow_redirects=True, headers=_SCRAPE_HEADERS, verify=False)
        if resp.status_code == 200:
            return resp.content
    except Exception as exc:
        log.warning("_fetch_html httpx error: %s", exc)

    return None


def _try_wp_rest_api(base_url: str, category_url: str, max_items: int = 10) -> list[dict]:
    """Intenta obtener artículos via WordPress REST API. Devuelve [] si el sitio no es WP o falla."""
    from urllib.parse import urlparse, urlencode

    parsed = urlparse(category_url)
    # Extraer slugs de la ruta: /tema/policiales-judiciales/policiales/ → ["tema", "policiales-judiciales", "policiales"]
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]

    api_base = f"{base_url}/wp-json/wp/v2"
    _api_headers = {"User-Agent": _SCRAPE_HEADERS["User-Agent"], "Accept": "application/json"}

    def _api_get(endpoint: str, params: dict) -> list | dict | None:
        """GET al WP REST API — intenta cloudscraper primero, luego httpx."""
        try:
            import cloudscraper
            scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
            r = scraper.get(endpoint, params=params, timeout=15, headers=_api_headers)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        try:
            r = httpx.get(endpoint, params=params, timeout=15, verify=False, headers=_api_headers)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    # Resolver cat_id desde el último slug del path (más específico primero)
    cat_id = None
    for slug in reversed(path_parts):
        data = _api_get(f"{api_base}/categories", {"slug": slug, "per_page": 1})
        if data and isinstance(data, list) and data[0].get("id"):
            cat_id = data[0]["id"]
            log.info("WP REST API: categoría '%s' → ID %s", slug, cat_id)
            break

    if not cat_id:
        log.debug("_try_wp_rest_api: no se encontró cat_id para %s", category_url)
        return []

    posts = _api_get(
        f"{api_base}/posts",
        {"per_page": max_items, "_embed": 1, "orderby": "date", "order": "desc", "categories": cat_id},
    )
    if not isinstance(posts, list):
        return []

    items = []
    for post in posts:
        link = post.get("link", "")
        title = BeautifulSoup(post.get("title", {}).get("rendered", ""), "html.parser").get_text().strip()

        # Usar content.rendered (artículo completo) si está disponible; excerpt como último recurso
        content_html = post.get("content", {}).get("rendered", "")
        excerpt_html = post.get("excerpt", {}).get("rendered", "")

        if content_html and len(content_html) > 300:
            content_soup = BeautifulSoup(content_html, "html.parser")
            # Eliminar bloques no deseados del contenido
            for noise in content_soup(["script", "style", "figure.wp-block-embed",
                                       "div.sharedaddy", "div.jp-relatedposts",
                                       "div.yarpp-related", "div.related-posts"]):
                noise.decompose()
            paras = [p.get_text(strip=True) for p in content_soup.find_all("p") if p.get_text(strip=True)]
            body = "\n\n".join(paras) if paras else content_soup.get_text(separator="\n", strip=True)
        else:
            body = BeautifulSoup(excerpt_html, "html.parser").get_text(strip=True)

        # Imagen desde _embedded
        image_url = None
        embedded = post.get("_embedded", {})
        media = embedded.get("wp:featuredmedia", [{}])
        if media and isinstance(media, list) and media[0].get("source_url"):
            image_url = _upgrade_wp_thumbnail(media[0]["source_url"])

        # Fecha
        published_at = None
        date_str = post.get("date_gmt") or post.get("date")
        if date_str:
            try:
                published_at = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except Exception:
                pass

        if not link or not title:
            continue

        items.append({
            "guid": link,
            "title": title,
            "link": link,
            "body": body,
            "published_at": published_at,
            "image_url": image_url,
            "needs_scraping": True,
        })

    log.info("WP REST API ok: %d artículos desde %s", len(items), base_url)
    return items


# Selectores genéricos para encontrar artículos en páginas de categoría
_ARTICLE_CONTAINERS = re.compile(
    r"article[-_]card|post[-_]card|news[-_]card|nota[-_]card|"
    r"entry[-_]preview|post[-_]preview|article[-_]item|post[-_]item|"
    r"article[-_]thumb|tdb-block-inner|td_module|mvp-blog-story",
    re.I,
)


def _scrape_category_html(url: str, max_items: int = 10) -> list[dict]:
    """Extrae artículos de una página de categoría parseando el HTML."""
    html = _fetch_html(url)
    if not html:
        log.warning("_scrape_category_html: no se pudo descargar %s", url)
        return []

    from urllib.parse import urlparse, urljoin
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    soup = BeautifulSoup(html, "html.parser")

    # Quitar nav, footer, aside y publicidad
    for tag in soup(["nav", "footer", "aside", "script", "style"]):
        tag.decompose()

    candidates: list[dict] = []

    def _resolve_img(container) -> str | None:
        """Busca la mejor imagen en un contenedor: lazy attrs primero, luego src."""
        img = container.find("img")
        if not img:
            return None
        for attr in _LAZY_ATTRS:
            val = img.get(attr, "").strip()
            if val and val.startswith("http") and not any(x in val.lower() for x in _IMG_SKIP):
                return _upgrade_wp_thumbnail(val)
        src = img.get("src", "").strip()
        if src and src.startswith("http") and not any(x in src.lower() for x in _IMG_SKIP):
            return _upgrade_wp_thumbnail(src)
        return None

    # Estrategia 1: <article> tags
    for art in soup.find_all("article"):
        a = art.find("a", href=True)
        # Patrón C5N: el <a> envuelve al <article> (es el contenedor padre)
        if not a and art.parent and art.parent.name == "a" and art.parent.get("href"):
            a = art.parent
        heading = art.find(re.compile(r"^h[1-4]$"))
        if not a:
            continue
        href = urljoin(base, a["href"])
        title = (heading.get_text(strip=True) if heading else a.get_text(strip=True))[:200]
        img_url = _resolve_img(art.parent if a is art.parent else art)
        if title and href and href.startswith("http"):
            candidates.append({"title": title, "link": href, "image_url": img_url})

    # Estrategia 2: contenedores con clases de card
    if not candidates:
        for div in soup.find_all(class_=_ARTICLE_CONTAINERS):
            a = div.find("a", href=True)
            if not a and div.parent and div.parent.name == "a" and div.parent.get("href"):
                a = div.parent
            heading = div.find(re.compile(r"^h[1-5]$"))
            if not a:
                continue
            href = urljoin(base, a["href"])
            title = (heading.get_text(strip=True) if heading else a.get_text(strip=True))[:200]
            img_url = _resolve_img(div.parent if a is getattr(div, "parent", None) else div)
            if title and href and href.startswith("http"):
                candidates.append({"title": title, "link": href, "image_url": img_url})

    # Estrategia 3: h2/h3 con links dentro del main
    if not candidates:
        main = soup.find(["main", "div"], id=re.compile(r"main|content|primary", re.I)) or soup
        for tag in main.find_all(re.compile(r"^h[2-4]$")):
            a = tag.find("a", href=True)
            if not a:
                continue
            href = urljoin(base, a["href"])
            title = tag.get_text(strip=True)[:200]
            if title and href and href.startswith("http") and href != url:
                candidates.append({"title": title, "link": href, "image_url": None})

    # Deduplicar por URL y limitar
    seen: set[str] = set()
    items = []
    for c in candidates:
        if c["link"] in seen or c["link"] == url:
            continue
        seen.add(c["link"])
        items.append({
            "guid": c["link"],
            "title": c["title"],
            "link": c["link"],
            "body": "",
            "published_at": None,
            "image_url": c["image_url"],
            "needs_scraping": True,  # siempre scrapear el artículo completo
        })
        if len(items) >= max_items:
            break

    log.info("_scrape_category_html: %d artículos desde %s", len(items), url)
    return items


def scrape_category_page(url: str) -> list[dict]:
    """Extrae los últimos artículos de una página de categoría web.

    Estrategias (en orden de preferencia):
    1. WordPress REST API (sin Cloudflare, datos completos)
    2. Scraping HTML con cloudscraper (Cloudflare) o httpx
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # 1. Intentar WordPress REST API
    items = _try_wp_rest_api(base_url, url)
    if items:
        return items

    # 2. HTML scraping
    return _scrape_category_html(url)


def test_web_source(url: str) -> tuple[bool, str]:
    """Prueba una URL de categoría web. Devuelve (ok, mensaje)."""
    try:
        items = scrape_category_page(url)
        if not items:
            return False, "No se encontraron artículos en esa URL."
        return True, f"Fuente válida — {len(items)} artículos encontrados."
    except Exception as exc:
        return False, str(exc)
