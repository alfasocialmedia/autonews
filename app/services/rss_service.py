from __future__ import annotations

import json
import logging
import re
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
]


_MIN_IMAGE_WIDTH = 400  # px mínimos para aceptar una imagen del feed


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
            return url

    # media_thumbnail omitido: son miniaturas pequeñas; se prefiere og:image al scrapear

    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image/"):
            return enc.get("href") or enc.get("url")

    html = ""
    if entry.get("content"):
        html = entry.content[0].get("value", "")
    if not html:
        html = entry.get("summary", "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)

    return None


def _is_comment_entry(entry) -> bool:
    """Detecta entradas de comentarios en feeds RSS (WordPress, Disqus, etc.)."""
    for field in ("id", "link"):
        url = entry.get(field, "")
        if re.search(r'[#?&/]comment|replytocom|#respond', url, re.I):
            return True
    return entry.get("type") == "comment"


def _extract_og_image(soup: BeautifulSoup) -> str | None:
    for attr in ({"property": "og:image"}, {"name": "twitter:image"}):
        tag = soup.find("meta", attrs=attr)
        if tag and tag.get("content"):
            return tag["content"]
    return None


_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "DNT": "1",
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


def scrape_full_article(url: str) -> tuple[str, str | None]:
    """Extrae el texto completo de un artículo y su og:image. Devuelve (texto, imagen_url)."""
    try:
        resp = httpx.get(
            url,
            timeout=_SCRAPE_TIMEOUT,
            follow_redirects=True,
            headers=_SCRAPE_HEADERS,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.warning("No se pudo scrapear %s: %s", url, exc)
        return "", None

    soup = BeautifulSoup(resp.text, "html.parser")
    og_image = _extract_og_image(soup)

    # 1. Preferir JSON-LD: contiene el artículo completo sin paywall ni JS
    jsonld_text = _extract_jsonld_text(soup)
    if jsonld_text:
        lines = [l.strip() for l in jsonld_text.splitlines() if l.strip()]
        return "\n".join(lines)[:12000], og_image

    # 2. Fallback: scraping HTML tradicional
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

    article = (
        soup.find(attrs={"itemprop": "articleBody"})
        or soup.find("article")
        or soup.find("main")
        or soup.find("div", class_=re.compile(r"(article|content|entry|post|nota|cuerpo|body|story)", re.I))
        or soup.find("div", id=re.compile(r"(article|content|entry|post|nota|cuerpo|body|story)", re.I))
    )

    container = article if article else soup
    text = container.get_text(separator="\n", strip=True)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return "\n".join(lines)[:12000], og_image


def fetch_rss_items(feed_url: str) -> list[dict]:
    """Descarga y parsea un feed RSS. Devuelve lista de items normalizados."""
    feed = feedparser.parse(feed_url, agent="AutoNews/1.0 (+https://autonews.local)")

    if feed.bozo and not feed.entries:
        raise ValueError(f"Feed invalido o inaccesible: {feed.bozo_exception}")

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

        items.append({
            "guid": guid,
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", ""),
            "body": content,
            "published_at": published_at,
            "image_url": _extract_image_url(entry),
            "needs_scraping": len(content) < _MIN_BODY_LENGTH,
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
