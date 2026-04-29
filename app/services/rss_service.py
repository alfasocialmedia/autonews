from __future__ import annotations

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


def _extract_image_url(entry) -> str | None:
    for mc in entry.get("media_content", []):
        url = mc.get("url", "")
        if url and (mc.get("type", "").startswith("image/") or re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, re.I)):
            return url

    for mt in entry.get("media_thumbnail", []):
        if mt.get("url"):
            return mt["url"]

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


def _extract_og_image(soup: BeautifulSoup) -> str | None:
    for attr in ({"property": "og:image"}, {"name": "twitter:image"}):
        tag = soup.find("meta", attrs=attr)
        if tag and tag.get("content"):
            return tag["content"]
    return None


def scrape_full_article(url: str) -> tuple[str, str | None]:
    """Extrae el texto completo de un artículo y su og:image. Devuelve (texto, imagen_url)."""
    try:
        resp = httpx.get(
            url,
            timeout=_SCRAPE_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AutoNews/1.0)"},
        )
        resp.raise_for_status()
    except Exception as exc:
        log.warning("No se pudo scrapear %s: %s", url, exc)
        return "", None

    soup = BeautifulSoup(resp.text, "html.parser")
    og_image = _extract_og_image(soup)

    # Eliminar ruido: scripts, estilos, nav, publicidad
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    for sel in _NOISE_SELECTORS:
        try:
            for el in soup.select(sel):
                el.decompose()
        except Exception:
            pass

    # Eliminar links sueltos que sean publicidad (solo anchor sin texto relevante)
    for a in soup.find_all("a"):
        txt = (a.get_text() or "").strip()
        if len(txt) < 4:
            a.decompose()

    # Intentar encontrar el contenedor principal del artículo
    article = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"itemprop": "articleBody"})
        or soup.find("div", class_=re.compile(r"(article|content|entry|post|nota|cuerpo|body|story)", re.I))
        or soup.find("div", id=re.compile(r"(article|content|entry|post|nota|cuerpo|body|story)", re.I))
    )

    container = article if article else soup
    text = container.get_text(separator="\n", strip=True)

    # Limpiar líneas vacías duplicadas
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)

    return text[:8000], og_image


def fetch_rss_items(feed_url: str) -> list[dict]:
    """Descarga y parsea un feed RSS. Devuelve lista de items normalizados."""
    feed = feedparser.parse(feed_url, agent="AutoNews/1.0 (+https://autonews.local)")

    if feed.bozo and not feed.entries:
        raise ValueError(f"Feed invalido o inaccesible: {feed.bozo_exception}")

    items = []
    for entry in feed.entries:
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
