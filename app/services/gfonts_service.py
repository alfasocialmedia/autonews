"""
Descarga y cachea fuentes de Google Fonts (TTF) desde el mirror de GitHub.
Las fuentes se guardan en /app/data/fonts (Docker) o app/static/fonts/gfonts (dev).
"""
from __future__ import annotations

import logging
import os
import pathlib
import urllib.request as _ur

log = logging.getLogger(__name__)

_GITHUB_BASE = "https://raw.githubusercontent.com/google/fonts/main"
_BUNDLED_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "fonts")

# Rutas de fuentes bundleadas en el repo (disponibles sin descarga)
_BUNDLED: dict[tuple[str, str], str] = {
    ("Montserrat", "bold"):         os.path.join(_BUNDLED_DIR, "Montserrat-Bold.ttf"),
    ("Playfair Display", "bold"):   os.path.join(_BUNDLED_DIR, "PlayfairDisplay-Bold.ttf"),
    ("Oswald", "bold"):             os.path.join(_BUNDLED_DIR, "Oswald-Bold.ttf"),
    ("Nunito", "bold"):             os.path.join(_BUNDLED_DIR, "Nunito-Bold.ttf"),
}

# Catálogo de fuentes de Google Fonts → ruta relativa en el mirror de GitHub
# weight keys: "regular" | "medium" | "bold" | "extrabold"
CATALOG: dict[str, dict] = {
    # ── Sans-serif ────────────────────────────────────────────────────────────
    "Montserrat": {
        "label": "Montserrat",
        "category": "Sans-serif",
        "weights": {
            "regular":   "ofl/montserrat/static/Montserrat-Regular.ttf",
            "medium":    "ofl/montserrat/static/Montserrat-Medium.ttf",
            "bold":      "ofl/montserrat/static/Montserrat-Bold.ttf",
            "extrabold": "ofl/montserrat/static/Montserrat-ExtraBold.ttf",
        },
    },
    "Poppins": {
        "label": "Poppins",
        "category": "Sans-serif",
        "weights": {
            "regular":   "ofl/poppins/Poppins-Regular.ttf",
            "medium":    "ofl/poppins/Poppins-Medium.ttf",
            "bold":      "ofl/poppins/Poppins-Bold.ttf",
            "extrabold": "ofl/poppins/Poppins-ExtraBold.ttf",
        },
    },
    "Roboto": {
        "label": "Roboto",
        "category": "Sans-serif",
        "weights": {
            "regular":   "apache/roboto/static/Roboto-Regular.ttf",
            "medium":    "apache/roboto/static/Roboto-Medium.ttf",
            "bold":      "apache/roboto/static/Roboto-Bold.ttf",
            "extrabold": "apache/roboto/static/Roboto-Black.ttf",
        },
    },
    "Open Sans": {
        "label": "Open Sans",
        "category": "Sans-serif",
        "weights": {
            "regular":   "ofl/opensans/static/OpenSans-Regular.ttf",
            "medium":    "ofl/opensans/static/OpenSans-Medium.ttf",
            "bold":      "ofl/opensans/static/OpenSans-Bold.ttf",
            "extrabold": "ofl/opensans/static/OpenSans-ExtraBold.ttf",
        },
    },
    "Lato": {
        "label": "Lato",
        "category": "Sans-serif",
        "weights": {
            "regular":   "ofl/lato/Lato-Regular.ttf",
            "medium":    "ofl/lato/Lato-Regular.ttf",
            "bold":      "ofl/lato/Lato-Bold.ttf",
            "extrabold": "ofl/lato/Lato-Black.ttf",
        },
    },
    "Raleway": {
        "label": "Raleway",
        "category": "Sans-serif",
        "weights": {
            "regular":   "ofl/raleway/static/Raleway-Regular.ttf",
            "medium":    "ofl/raleway/static/Raleway-Medium.ttf",
            "bold":      "ofl/raleway/static/Raleway-Bold.ttf",
            "extrabold": "ofl/raleway/static/Raleway-ExtraBold.ttf",
        },
    },
    "Nunito": {
        "label": "Nunito",
        "category": "Sans-serif",
        "weights": {
            "regular":   "ofl/nunito/static/Nunito-Regular.ttf",
            "medium":    "ofl/nunito/static/Nunito-Medium.ttf",
            "bold":      "ofl/nunito/static/Nunito-Bold.ttf",
            "extrabold": "ofl/nunito/static/Nunito-ExtraBold.ttf",
        },
    },
    "Inter": {
        "label": "Inter",
        "category": "Sans-serif",
        "weights": {
            "regular":   "ofl/inter/static/Inter-Regular.ttf",
            "medium":    "ofl/inter/static/Inter-Medium.ttf",
            "bold":      "ofl/inter/static/Inter-Bold.ttf",
            "extrabold": "ofl/inter/static/Inter-ExtraBold.ttf",
        },
    },
    # ── Serif ─────────────────────────────────────────────────────────────────
    "Playfair Display": {
        "label": "Playfair Display",
        "category": "Serif",
        "weights": {
            "regular":   "ofl/playfairdisplay/static/PlayfairDisplay-Regular.ttf",
            "medium":    "ofl/playfairdisplay/static/PlayfairDisplay-Medium.ttf",
            "bold":      "ofl/playfairdisplay/static/PlayfairDisplay-Bold.ttf",
            "extrabold": "ofl/playfairdisplay/static/PlayfairDisplay-Black.ttf",
        },
    },
    "Merriweather": {
        "label": "Merriweather",
        "category": "Serif",
        "weights": {
            "regular":   "ofl/merriweather/Merriweather-Regular.ttf",
            "medium":    "ofl/merriweather/Merriweather-Regular.ttf",
            "bold":      "ofl/merriweather/Merriweather-Bold.ttf",
            "extrabold": "ofl/merriweather/Merriweather-Black.ttf",
        },
    },
    "Lora": {
        "label": "Lora",
        "category": "Serif",
        "weights": {
            "regular":   "ofl/lora/static/Lora-Regular.ttf",
            "medium":    "ofl/lora/static/Lora-Medium.ttf",
            "bold":      "ofl/lora/static/Lora-Bold.ttf",
            "extrabold": "ofl/lora/static/Lora-Bold.ttf",
        },
    },
    # ── Display / Condensada ──────────────────────────────────────────────────
    "Oswald": {
        "label": "Oswald",
        "category": "Display",
        "weights": {
            "regular":   "ofl/oswald/static/Oswald-Regular.ttf",
            "medium":    "ofl/oswald/static/Oswald-Medium.ttf",
            "bold":      "ofl/oswald/static/Oswald-Bold.ttf",
            "extrabold": "ofl/oswald/static/Oswald-ExtraBold.ttf",
        },
    },
    "Bebas Neue": {
        "label": "Bebas Neue",
        "category": "Display",
        "weights": {
            "regular":   "ofl/bebasneu/BebasNeue-Regular.ttf",
            "medium":    "ofl/bebasneu/BebasNeue-Regular.ttf",
            "bold":      "ofl/bebasneu/BebasNeue-Regular.ttf",
            "extrabold": "ofl/bebasneu/BebasNeue-Regular.ttf",
        },
    },
    "Anton": {
        "label": "Anton",
        "category": "Display",
        "weights": {
            "regular":   "ofl/anton/Anton-Regular.ttf",
            "medium":    "ofl/anton/Anton-Regular.ttf",
            "bold":      "ofl/anton/Anton-Regular.ttf",
            "extrabold": "ofl/anton/Anton-Regular.ttf",
        },
    },
}

# Valores legacy del campo font_family (antes "sans"/"serif"/etc.)
LEGACY_MAP: dict[str, str] = {
    "sans":    "Montserrat",
    "serif":   "Playfair Display",
    "impact":  "Oswald",
    "rounded": "Nunito",
}


def _cache_dir() -> str:
    pdir = pathlib.Path("/app/data/fonts")
    if pdir.parent.exists():
        pdir.mkdir(exist_ok=True)
        return str(pdir)
    ldir = pathlib.Path("app/static/fonts/gfonts")
    ldir.mkdir(parents=True, exist_ok=True)
    return str(ldir)


def _is_valid_ttf(data: bytes) -> bool:
    if len(data) < 4:
        return False
    # TTF, OTF (CFF), TTC
    return data[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"ttcf", b"true")


def get_font_path(family: str, weight: str = "bold") -> str | None:
    """Devuelve ruta local al TTF de la fuente, descargando si hace falta."""
    family = LEGACY_MAP.get(family, family)

    # 1. Intentar fuente bundleada (sin descarga)
    bundled = _BUNDLED.get((family, weight))
    if bundled and os.path.exists(bundled) and os.path.getsize(bundled) > 1000:
        return bundled

    info = CATALOG.get(family)
    if not info:
        return None

    weights = info["weights"]
    github_path = weights.get(weight) or weights.get("bold") or next(iter(weights.values()))

    safe_name = family.replace(" ", "_")
    cache_dir = _cache_dir()
    cache_file = os.path.join(cache_dir, f"{safe_name}-{weight}.ttf")

    # 2. Usar caché local si ya se descargó y es válido
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "rb") as f:
                header = f.read(8)
            if _is_valid_ttf(header):
                return cache_file
        except Exception:
            pass
        try:
            os.remove(cache_file)
        except Exception:
            pass

    # 3. Descargar desde GitHub google/fonts (2 intentos)
    url = f"{_GITHUB_BASE}/{github_path}"
    for attempt in range(2):
        try:
            req = _ur.Request(url, headers={"User-Agent": "AutoNews/1.0"})
            with _ur.urlopen(req, timeout=15) as r:
                data = r.read()
            if not _is_valid_ttf(data):
                log.warning("Archivo no válido para %s/%s (bytes: %s)", family, weight, data[:4].hex())
                break
            with open(cache_file, "wb") as f:
                f.write(data)
            log.info("Fuente descargada: %s %s → %s", family, weight, cache_file)
            return cache_file
        except Exception as exc:
            log.warning("Intento %d fallido al descargar %s %s: %s", attempt + 1, family, weight, exc)

    # 4. Fallback al bold de la misma familia (mismo estilo visual, distinto peso)
    if weight != "bold":
        bold_bundled = _BUNDLED.get((family, "bold"))
        if bold_bundled and os.path.exists(bold_bundled) and os.path.getsize(bold_bundled) > 1000:
            log.warning("Usando bold bundled como fallback para %s %s", family, weight)
            return bold_bundled
        bold_cache = os.path.join(cache_dir, f"{safe_name}-bold.ttf")
        if os.path.exists(bold_cache):
            try:
                with open(bold_cache, "rb") as f:
                    header = f.read(8)
                if _is_valid_ttf(header):
                    log.warning("Usando bold cacheado como fallback para %s %s", family, weight)
                    return bold_cache
            except Exception:
                pass

    return None
