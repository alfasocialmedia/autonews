from __future__ import annotations

import logging
from datetime import datetime, timezone

import feedparser

log = logging.getLogger("rss_service")


def fetch_rss_items(feed_url: str) -> list[dict]:
    """Descarga y parsea un feed RSS. Devuelve lista de ítems normalizados."""
    feed = feedparser.parse(feed_url, agent="AutoNews/1.0 (+https://autonews.local)")

    if feed.bozo and not feed.entries:
        raise ValueError(f"Feed inválido o inaccesible: {feed.bozo_exception}")

    items = []
    for entry in feed.entries:
        guid = entry.get("id") or entry.get("link") or ""
        if not guid:
            continue

        # Contenido: preferir texto completo sobre resumen
        content = ""
        if entry.get("content"):
            content = entry.content[0].get("value", "")
        if not content:
            content = entry.get("summary", "")

        # Fecha de publicación
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
        })

    return items


def test_rss_feed(url: str) -> tuple[bool, str]:
    """Prueba si un feed RSS es válido y accesible."""
    try:
        items = fetch_rss_items(url)
        if not items:
            return False, "El feed no contiene ítems o no es accesible."
        return True, f"Feed válido — {len(items)} artículos encontrados."
    except Exception as exc:
        return False, str(exc)
