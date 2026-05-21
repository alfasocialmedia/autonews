"""
Genera la imagen para Instagram:
  - Redimensiona a 1080×1440 (4:5) recortando desde el centro
  - Agrega degradado oscuro en la parte inferior
  - Superpone el texto del título (con ajuste de línea automático)
  - Superpone el logo del medio (si existe) en la esquina configurada
  - Dibuja franja de color en la parte inferior con texto personalizado
Retorna bytes JPEG listos para subir.
"""
from __future__ import annotations

import io
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

TARGET_W = 1080
TARGET_H = 1440
FONT_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "fonts")
LOGO_MARGIN = 40
LOGO_MAX_SIZE = 180   # px — lado máximo del logo
BANNER_HEIGHT = 72    # px — alto de la franja inferior
BANNER_MARGIN = 28    # px — margen horizontal del banner respecto al borde


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        os.path.join(FONT_DIR, "NotoSans-Bold.ttf"),
        os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf"),
        # Linux (Docker)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        # Windows (desarrollo local)
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/verdanab.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)


def _crop_center(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _add_gradient(
    img: Image.Image,
    height: int,
    color: str = "#000000",
    max_opacity: int = 200,
) -> Image.Image:
    r, g, b = _hex_to_rgb(color)
    gradient = Image.new("RGBA", (img.width, height), (r, g, b, 0))
    draw = ImageDraw.Draw(gradient)
    for y in range(height):
        # Curva cuadrática: se oscurece más rápido hacia el fondo
        t = y / height
        alpha = int(max_opacity * (t ** 1.6))
        draw.line([(0, y), (img.width, y)], fill=(r, g, b, alpha))
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    overlay.paste(gradient, (0, base.height - height))
    return Image.alpha_composite(base, overlay).convert("RGB")


def _draw_title(
    img: Image.Image,
    title: str,
    font_size: int = 62,
    text_color: str = "#ffffff",
    bottom_offset: int = 80,
) -> Image.Image:
    """Dibuja el título con sombra múltiple para máxima legibilidad."""
    font = _load_font(font_size)
    tr, tg, tb = _hex_to_rgb(text_color)
    max_chars = max(18, int(TARGET_W / (font_size * 0.55)))
    lines = textwrap.wrap(title, width=max_chars)[:4]
    line_height = int(font_size * 1.25)
    total_height = len(lines) * line_height
    y = img.height - total_height - bottom_offset
    padding_x = 50

    # Convertir a RGBA para poder componer la sombra con transparencia
    base = img.convert("RGBA")
    txt_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(txt_layer)

    for line in lines:
        # Sombra difusa (3 desplazamientos)
        for dx, dy in [(3, 3), (2, 2), (1, 1)]:
            draw.text((padding_x + dx, y + dy), line, font=font, fill=(0, 0, 0, 160))
        # Texto principal
        draw.text((padding_x, y), line, font=font, fill=(tr, tg, tb, 255))
        y += line_height

    return Image.alpha_composite(base, txt_layer).convert("RGB")


def _draw_banner(
    img: Image.Image,
    text: str,
    bg_color: str = "#e53935",
    text_color: str = "#ffffff",
) -> Image.Image:
    """Dibuja una franja de color con texto centrado en la parte inferior, estilo píldora."""
    if not text or not text.strip():
        return img

    draw = ImageDraw.Draw(img)
    font_size = 34
    font = _load_font(font_size)

    # Medir el ancho del texto para determinar el tamaño de la píldora
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    except AttributeError:
        text_w, text_h = draw.textsize(text, font=font)

    pad_x = 48
    pad_y = 14
    pill_w = min(text_w + pad_x * 2, img.width - BANNER_MARGIN * 2)
    pill_h = text_h + pad_y * 2
    radius = pill_h // 2

    # Centrar la píldora horizontalmente, posicionar en la parte inferior
    x0 = (img.width - pill_w) // 2
    y0 = img.height - pill_h - BANNER_MARGIN
    x1 = x0 + pill_w
    y1 = y0 + pill_h

    br, bg, bb = _hex_to_rgb(bg_color)
    tr, tg, tb = _hex_to_rgb(text_color)

    # Dibujar la píldora con bordes redondeados
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=(br, bg, bb))

    # Texto centrado dentro de la píldora
    tx = x0 + (pill_w - text_w) // 2
    ty = y0 + (pill_h - text_h) // 2 - (bbox[1] if 'bbox' in dir() else 0)
    draw.text((tx, ty), text, font=font, fill=(tr, tg, tb))
    return img


def _paste_logo(img: Image.Image, logo_path: str, position: str) -> Image.Image:
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
    gradient_color: str = "#000000",
    gradient_opacity: int = 200,
    gradient_height: int = 480,
    font_size: int = 62,
    text_color: str = "#ffffff",
    banner_text: str | None = None,
    banner_color: str = "#e53935",
    banner_text_color: str = "#ffffff",
) -> bytes:
    """
    Pipeline completo: recibe bytes de imagen, devuelve JPEG 1080×1440 con
    título, logo y franja inferior superpuestos.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = _crop_center(img, TARGET_W, TARGET_H)
    img = _add_gradient(img, gradient_height, color=gradient_color, max_opacity=gradient_opacity)

    # Si hay banner, el título sube para no tapar la franja
    title_bottom = (BANNER_HEIGHT + BANNER_MARGIN + 20) if banner_text else 80
    img = _draw_title(img, title, font_size=font_size, text_color=text_color, bottom_offset=title_bottom)

    if banner_text:
        img = _draw_banner(img, banner_text, bg_color=banner_color, text_color=banner_text_color)

    if logo_path:
        img = _paste_logo(img, logo_path, logo_position)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue()
