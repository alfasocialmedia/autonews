"""
Genera la imagen para Instagram:
  - Redimensiona a 1080×1350 (4:5) recortando desde el centro
  - Agrega degradado oscuro en la parte inferior
  - Superpone el texto del título (con ajuste de línea automático)
  - Superpone el logo del medio (si existe) en la esquina configurada
Retorna bytes JPEG listos para subir.
"""
from __future__ import annotations

import io
import os
import textwrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont

TARGET_W = 1080
TARGET_H = 1350
FONT_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "fonts")
LOGO_MARGIN = 40
LOGO_MAX_SIZE = 180   # px — lado máximo del logo
GRADIENT_HEIGHT = 480  # px — alto del degradado inferior


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Intenta cargar una fuente TrueType; si no existe usa la built-in."""
    candidates = [
        os.path.join(FONT_DIR, "NotoSans-Bold.ttf"),
        os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _crop_center(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Redimensiona manteniendo aspecto y recorta al centro."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _add_gradient(img: Image.Image, height: int) -> Image.Image:
    """Agrega degradado negro de abajo hacia arriba en los últimos `height` px."""
    gradient = Image.new("RGBA", (img.width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)
    for y in range(height):
        alpha = int(200 * (y / height))
        draw.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    overlay.paste(gradient, (0, base.height - height))
    return Image.alpha_composite(base, overlay).convert("RGB")


def _draw_title(img: Image.Image, title: str, font_size: int = 62) -> Image.Image:
    """Dibuja el título con sombra en la zona inferior de la imagen."""
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)
    max_chars = max(18, int(TARGET_W / (font_size * 0.55)))
    lines = textwrap.wrap(title, width=max_chars)[:4]  # máx 4 líneas
    line_height = font_size + 10
    total_height = len(lines) * line_height
    y = img.height - total_height - 80
    padding_x = 50

    for line in lines:
        # Sombra
        draw.text((padding_x + 2, y + 2), line, font=font, fill=(0, 0, 0, 180))
        # Texto blanco
        draw.text((padding_x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height
    return img


def _paste_logo(img: Image.Image, logo_path: str, position: str) -> Image.Image:
    """Superpone el logo con tamaño máximo LOGO_MAX_SIZE en la esquina indicada."""
    if not logo_path or not os.path.exists(logo_path):
        return img
    try:
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((LOGO_MAX_SIZE, LOGO_MAX_SIZE), Image.LANCZOS)
        lw, lh = logo.size
        m = LOGO_MARGIN
        positions = {
            "top-left":     (m, m),
            "top-right":    (img.width - lw - m, m),
            "bottom-left":  (m, img.height - lh - m),
            "bottom-right": (img.width - lw - m, img.height - lh - m),
        }
        x, y = positions.get(position, positions["bottom-right"])
        base = img.convert("RGBA")
        base.paste(logo, (x, y), mask=logo)
        return base.convert("RGB")
    except Exception:
        return img


def build_instagram_image(
    image_bytes: bytes,
    title: str,
    logo_path: str | None = None,
    logo_position: str = "bottom-right",
) -> bytes:
    """
    Pipeline completo: recibe bytes de imagen, devuelve JPEG 1080×1350 con
    título y logo superpuestos.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = _crop_center(img, TARGET_W, TARGET_H)
    img = _add_gradient(img, GRADIENT_HEIGHT)
    img = _draw_title(img, title)
    if logo_path:
        img = _paste_logo(img, logo_path, logo_position)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue()
