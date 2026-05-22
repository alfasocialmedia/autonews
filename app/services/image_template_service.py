"""
Genera la imagen para Instagram:
  - Redimensiona a 1080×1440 (4:5) recortando desde el centro
  - Agrega degradado oscuro cuadrático en la parte inferior
  - Superpone el texto del título con alineación y fuente configurables
  - Superpone el logo del medio (si existe) en la esquina configurada
  - Dibuja franja de color en la parte inferior con texto personalizado
Retorna bytes JPEG listos para subir.
"""
from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw, ImageFont

TARGET_W = 1080
TARGET_H = 1440
FONT_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "fonts")
LOGO_MARGIN = 40
LOGO_MAX_SIZE = 180
BANNER_HEIGHT = 72
BANNER_MARGIN = 28

# Fuentes bundleadas como último recurso
_BUNDLED_FALLBACKS = [
    os.path.join(FONT_DIR, "Montserrat-Bold.ttf"),
    os.path.join(FONT_DIR, "Oswald-Bold.ttf"),
    os.path.join(FONT_DIR, "PlayfairDisplay-Bold.ttf"),
    os.path.join(FONT_DIR, "Nunito-Bold.ttf"),
]


def _load_font(size: int, family: str = "Montserrat", weight: str = "bold") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Carga fuente desde gfonts_service (con descarga y caché) con fallback a bundleadas."""
    from app.services.gfonts_service import get_font_path
    path = get_font_path(family, weight)
    if path and os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    # Fallback a fuentes bundleadas
    for p in _BUNDLED_FALLBACKS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
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
        t = y / height
        alpha = int(max_opacity * (t ** 1.2))
        draw.line([(0, y), (img.width, y)], fill=(r, g, b, alpha))
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    overlay.paste(gradient, (0, base.height - height))
    return Image.alpha_composite(base, overlay).convert("RGB")


def _measure_text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0]
    except AttributeError:
        w, _ = draw.textsize(text, font=font)  # type: ignore[attr-defined]
        return w


def _wrap_by_pixels(title: str, font, max_px: int, max_lines: int = 4) -> list[str]:
    """Divide el título en líneas que no superen max_px de ancho real (medición PIL)."""
    tmp_img = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(tmp_img)
    words = title.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if _measure_text_w(draw, candidate, font) <= max_px or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(" ".join(current))
    return lines[:max_lines]


def _draw_title(
    img: Image.Image,
    title: str,
    font_size: int = 62,
    text_color: str = "#ffffff",
    text_align: str = "left",
    font_family: str = "Montserrat",
    font_weight: str = "bold",
    text_bg_color: str = "#000000",
    text_bg_opacity: int = 0,
    text_bg_padding_x: int = 40,
    text_bg_padding_y: int = 18,
    title_max_lines: int = 4,
    text_box_x_pct: int = 0,
    text_box_y_pct: int = 70,
    text_box_w_pct: int = 100,
) -> Image.Image:
    """Dibuja el título dentro de una caja posicionable.

    La caja define el área del título: posición (x_pct, y_pct) y ancho (w_pct)
    en porcentaje del tamaño de la imagen. El alto se ajusta automáticamente
    al contenido. El texto nunca se corta a mitad de palabra ni desborda la caja.
    """
    font = _load_font(font_size, family=font_family, weight=font_weight)
    tr, tg, tb = _hex_to_rgb(text_color)

    # Calcular dimensiones de la caja en píxeles
    box_x = int(TARGET_W * max(0, min(95, text_box_x_pct)) / 100)
    box_y = int(TARGET_H * max(0, min(95, text_box_y_pct)) / 100)
    box_w = int(TARGET_W * max(10, min(100, text_box_w_pct)) / 100)
    # La caja no puede salirse por la derecha
    box_w = min(box_w, TARGET_W - box_x)

    pad_x = max(0, text_bg_padding_x)
    pad_y = max(0, text_bg_padding_y)
    usable_w = max(1, box_w - 2 * pad_x)

    lines = _wrap_by_pixels(title, font, usable_w, max_lines=max(1, title_max_lines))
    line_height = int(font_size * 1.25)
    total_text_h = len(lines) * line_height

    # Alto de la caja: se ajusta exactamente al texto + padding vertical
    box_h = total_text_h + 2 * pad_y
    # No desbordar por abajo
    if box_y + box_h > TARGET_H:
        box_h = max(line_height, TARGET_H - box_y)

    base = img.convert("RGBA")
    txt_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(txt_layer)

    if text_bg_opacity > 0:
        bgr, bgg, bgb = _hex_to_rgb(text_bg_color)
        draw.rectangle(
            [box_x, box_y, box_x + box_w, box_y + box_h],
            fill=(bgr, bgg, bgb, min(255, text_bg_opacity)),
        )

    y = box_y + pad_y
    for line in lines:
        line_w = _measure_text_w(draw, line, font)
        if text_align == "center":
            x = box_x + pad_x + (usable_w - line_w) // 2
        elif text_align == "right":
            x = box_x + box_w - pad_x - line_w
        else:
            x = box_x + pad_x

        for dx, dy in [(3, 3), (2, 2), (1, 1)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y), line, font=font, fill=(tr, tg, tb, 255))
        y += line_height

    return Image.alpha_composite(base, txt_layer).convert("RGB")


def _draw_banner(
    img: Image.Image,
    text: str,
    bg_color: str = "#e53935",
    text_color: str = "#ffffff",
    banner_style: str = "pill",
    font_family: str = "Montserrat",
    font_weight: str = "bold",
    y_offset: int = 0,
    align: str = "center",
) -> Image.Image:
    """Dibuja la franja inferior con estilo configurable: 'pill', 'rect' o 'none'.
    y_offset > 0 sube la franja, < 0 la baja. align: 'left'|'center'|'right'."""
    if not text or not text.strip():
        return img

    font_size = 34
    font = _load_font(font_size, family=font_family, weight=font_weight)

    tmp_draw = ImageDraw.Draw(img)
    try:
        bbox = tmp_draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        text_top = bbox[1]
    except AttributeError:
        text_w, text_h = tmp_draw.textsize(text, font=font)  # type: ignore[attr-defined]
        text_top = 0

    tr, tg, tb = _hex_to_rgb(text_color)

    if banner_style == "none":
        # Solo texto con sombra, sin fondo
        if align == "left":
            tx = BANNER_MARGIN
        elif align == "right":
            tx = img.width - text_w - BANNER_MARGIN
        else:
            tx = (img.width - text_w) // 2
        ty = img.height - text_h - BANNER_MARGIN - y_offset - text_top
        base = img.convert("RGBA")
        txt_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(txt_layer)
        for dx, dy in [(3, 3), (2, 2), (1, 1)]:
            d.text((tx + dx, ty + dy), text, font=font, fill=(0, 0, 0, 200))
        d.text((tx, ty), text, font=font, fill=(tr, tg, tb, 255))
        return Image.alpha_composite(base, txt_layer).convert("RGB")

    br, bg_, bb = _hex_to_rgb(bg_color)
    pad_x = 48
    pad_y = 14
    box_w = min(text_w + pad_x * 2, img.width - BANNER_MARGIN * 2)
    box_h = text_h + pad_y * 2

    if align == "left":
        x0 = BANNER_MARGIN
    elif align == "right":
        x0 = img.width - box_w - BANNER_MARGIN
    else:
        x0 = (img.width - box_w) // 2
    y0 = img.height - box_h - BANNER_MARGIN - y_offset
    x1 = x0 + box_w
    y1 = y0 + box_h

    draw = ImageDraw.Draw(img)
    if banner_style == "rect":
        draw.rectangle([x0, y0, x1, y1], fill=(br, bg_, bb))
    else:
        # pill (default)
        radius = box_h // 2
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=(br, bg_, bb))

    tx = x0 + (box_w - text_w) // 2
    ty = y0 + (box_h - text_h) // 2 - text_top
    draw.text((tx, ty), text, font=font, fill=(tr, tg, tb))
    return img


def _draw_category_badge(
    img: Image.Image,
    category: str,
    bg_color: str = "#e53935",
    text_color: str = "#ffffff",
    font_family: str = "Montserrat",
    font_weight: str = "bold",
    x_percent: int = 0,
    y_percent: int = 0,
) -> Image.Image:
    """Dibuja el badge de categoría. x_percent 0=izq 100=der, y_percent 0=arriba 100=abajo."""
    if not category or not category.strip():
        return img

    text = category.upper().strip()
    font = _load_font(28, family=font_family, weight=font_weight)

    tmp_draw = ImageDraw.Draw(img)
    try:
        bbox = tmp_draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        text_top = bbox[1]
    except AttributeError:
        text_w, text_h = tmp_draw.textsize(text, font=font)  # type: ignore[attr-defined]
        text_top = 0

    pad_x, pad_y = 24, 10
    box_w = min(text_w + pad_x * 2, img.width - BANNER_MARGIN * 2)
    box_h = text_h + pad_y * 2
    margin = BANNER_MARGIN

    travel_x = max(0, img.width - box_w - margin * 2)
    travel_y = max(0, img.height - box_h - margin * 2)
    x0 = margin + travel_x * max(0, min(100, x_percent)) // 100
    y0 = margin + travel_y * max(0, min(100, y_percent)) // 100
    x1 = x0 + box_w
    y1 = y0 + box_h

    br, bg_, bb = _hex_to_rgb(bg_color)
    tr, tg, tb = _hex_to_rgb(text_color)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=box_h // 2, fill=(br, bg_, bb))
    tx = x0 + (box_w - text_w) // 2
    ty = y0 + (box_h - text_h) // 2 - text_top
    draw.text((tx, ty), text, font=font, fill=(tr, tg, tb))
    return img


def _paste_logo(img: Image.Image, logo_path: str, position: str, size: int = LOGO_MAX_SIZE) -> Image.Image:
    if not logo_path or not os.path.exists(logo_path):
        return img
    try:
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((size, size), Image.LANCZOS)
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
    logo_size: int = 180,
    gradient_color: str = "#000000",
    gradient_opacity: int = 200,
    gradient_height: int = 480,
    font_size: int = 62,
    text_color: str = "#ffffff",
    banner_text: str | None = None,
    banner_color: str = "#e53935",
    banner_text_color: str = "#ffffff",
    text_align: str = "left",
    title_y_offset: int = 0,   # legacy, no usado
    font_family: str = "Montserrat",
    font_weight: str = "bold",
    text_bg_color: str = "#000000",
    text_bg_opacity: int = 0,
    banner_style: str = "pill",
    banner_font_weight: str = "bold",
    banner_y_offset: int = 0,
    banner_align: str = "center",
    text_bg_padding_x: int = 40,
    text_bg_padding_y: int = 18,
    text_bg_full_width: bool = True,   # legacy, no usado
    title_max_lines: int = 4,
    category: str | None = None,
    show_category: bool = False,
    category_bg_color: str = "#e53935",
    category_text_color: str = "#ffffff",
    category_x_percent: int = 0,
    category_y_percent: int = 0,
    banner_font_family: str = "Montserrat",
    category_font_family: str = "Montserrat",
    text_box_x_pct: int = 0,
    text_box_y_pct: int = 70,
    text_box_w_pct: int = 100,
) -> bytes:
    """
    Pipeline completo: recibe bytes de imagen, devuelve JPEG 1080×1440.
    font_weight / banner_font_weight: "regular" | "medium" | "bold" | "extrabold"
    banner_style: "pill" | "rect" | "none"
    text_bg_opacity > 0: rectángulo detrás del título (caja posicionable).
    show_category: muestra badge de categoría en la esquina superior.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = _crop_center(img, TARGET_W, TARGET_H)
    img = _add_gradient(img, gradient_height, color=gradient_color, max_opacity=gradient_opacity)

    img = _draw_title(
        img, title,
        font_size=font_size, text_color=text_color,
        text_align=text_align,
        font_family=font_family, font_weight=font_weight,
        text_bg_color=text_bg_color, text_bg_opacity=text_bg_opacity,
        text_bg_padding_x=text_bg_padding_x,
        text_bg_padding_y=text_bg_padding_y,
        title_max_lines=title_max_lines,
        text_box_x_pct=text_box_x_pct,
        text_box_y_pct=text_box_y_pct,
        text_box_w_pct=text_box_w_pct,
    )

    if banner_text:
        img = _draw_banner(
            img, banner_text,
            bg_color=banner_color, text_color=banner_text_color,
            banner_style=banner_style,
            font_family=banner_font_family, font_weight=banner_font_weight,
            y_offset=banner_y_offset, align=banner_align,
        )

    if show_category and category:
        img = _draw_category_badge(
            img, category,
            bg_color=category_bg_color, text_color=category_text_color,
            font_family=category_font_family, font_weight=font_weight,
            x_percent=category_x_percent,
            y_percent=category_y_percent,
        )

    if logo_path:
        img = _paste_logo(img, logo_path, logo_position, size=logo_size)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue()
