from __future__ import annotations

import json
import logging
import re

from openai import OpenAI

log = logging.getLogger("groq_service")

PROVIDERS: dict[str, dict] = {
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "gemma2-9b-it",
            "mixtral-8x7b-32768",
        ],
    },
    "google_gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": [
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ],
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": [
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "deepseek-chat",
            "deepseek-reasoner",
        ],
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            "deepseek/deepseek-chat-v3-0324:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen3-235b-a22b:free",
            "google/gemini-2.0-flash-exp:free",
            "deepseek/deepseek-chat-v3-0324",
            "meta-llama/llama-3.3-70b-instruct",
            "qwen/qwen3-235b-a22b",
        ],
    },
    "together": {
        "label": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "models": [
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
            "deepseek-ai/DeepSeek-V3",
            "mistralai/Mixtral-8x7B-Instruct-v0.1",
        ],
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "base_url": "",
        "models": [
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            "claude-opus-4-6",
        ],
    },
    "custom": {
        "label": "Personalizado",
        "base_url": "",
        "models": [],
    },
}


def _get_client(api_key: str, provider: str = "groq", api_base_url: str | None = None) -> OpenAI:
    base_url = api_base_url or PROVIDERS.get(provider, PROVIDERS["groq"])["base_url"]
    return OpenAI(api_key=api_key, base_url=base_url)


def test_groq_connection(
    api_key: str,
    model: str,
    provider: str = "groq",
    api_base_url: str | None = None,
) -> tuple[bool, str]:
    try:
        if provider == "anthropic":
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Responde únicamente con la palabra: ok"}],
            )
            text = next(b.text for b in resp.content if b.type == "text")
            return True, text.strip()
        client = _get_client(api_key, provider, api_base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Responde únicamente con la palabra: ok"}],
            max_tokens=10,
        )
        return True, resp.choices[0].message.content.strip()
    except Exception as exc:
        return False, str(exc)


_VISION_MODELS: dict[str, str] = {
    "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
    "google_gemini": "gemini-2.0-flash",
    "openrouter": "google/gemini-2.0-flash-exp:free",
    "together": "meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo",
    "anthropic": "claude-sonnet-4-6",
}


def extract_image_text(
    api_key: str,
    image_bytes: bytes,
    mimetype: str = "image/jpeg",
    provider: str = "groq",
    api_base_url: str | None = None,
) -> str:
    """Extrae texto o descripción de una imagen con visión IA. Devuelve '' si falla."""
    import base64 as b64lib

    clean_mime = mimetype.split(";")[0].strip()
    b64 = b64lib.b64encode(image_bytes).decode()
    image_data_url = f"data:{clean_mime};base64,{b64}"

    vision_model = _VISION_MODELS.get(provider)
    actual_provider = provider
    actual_base_url = api_base_url
    if not vision_model:
        actual_provider = "groq"
        actual_base_url = None
        vision_model = _VISION_MODELS["groq"]

    prompt_text = (
        "Extraé el contenido de esta imagen en español.\n"
        "- Si es una noticia, tweet, screenshot o imagen con texto: transcribí el texto tal como aparece. "
        "Primera línea: el titular o headline. Líneas siguientes: el cuerpo del texto.\n"
        "- Ignorá elementos de interfaz: botones, íconos de 'Me gusta', 'Compartir', fechas de publicación, "
        "nombres de usuario '@', pie de página, marcas de agua.\n"
        "- Si la imagen no tiene texto relevante (es una fotografía de escena), "
        "describí el acontecimiento principal en una oración directa.\n"
        "- Respondé SOLO con el texto extraído. Sin introducción, sin frases como "
        "'La imagen muestra...', 'El texto dice...', 'En la imagen se puede ver...'."
    )

    try:
        if actual_provider == "anthropic":
            import anthropic as _anthropic
            aclient = _anthropic.Anthropic(api_key=api_key)
            resp = aclient.messages.create(
                model=vision_model,
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": clean_mime,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt_text},
                    ],
                }],
            )
            result = next(bl.text for bl in resp.content if bl.type == "text")
        else:
            client = _get_client(api_key, actual_provider, actual_base_url)
            resp = client.chat.completions.create(
                model=vision_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                        {"type": "text", "text": prompt_text},
                    ],
                }],
                max_tokens=1000,
                temperature=0.2,
            )
            result = resp.choices[0].message.content.strip()
        log.info("extract_image_text: %d chars (%s/%s)", len(result), actual_provider, vision_model)
        return result
    except Exception as exc:
        log.warning("extract_image_text error (%s/%s): %s", actual_provider, vision_model, exc)
        return ""


def transcribe_audio(api_key: str, audio_bytes: bytes, mimetype: str = "audio/ogg") -> str:
    """Transcribe audio con Groq Whisper (solo funciona con provider=groq). Devuelve '' si falla."""
    import io
    ext_map = {
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp4": "mp4",
        "audio/wav": "wav",
        "audio/webm": "webm",
        "audio/aac": "aac",
    }
    clean_mime = mimetype.split(";")[0].strip()
    ext = ext_map.get(clean_mime, "ogg")
    try:
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        result = client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=(f"audio.{ext}", io.BytesIO(audio_bytes), clean_mime),
            response_format="text",
            language="es",
        )
        return str(result).strip()
    except Exception as exc:
        log.warning("transcribe_audio error: %s", exc)
        return ""


_FWD_RE = re.compile(r"^(fwd?|re|fw)\s*:\s*", re.IGNORECASE)


def _clean_subject(subject: str) -> str:
    while True:
        cleaned = _FWD_RE.sub("", subject).strip()
        if cleaned == subject:
            return cleaned
        subject = cleaned


def _normalize_quotes(text: str) -> str:
    return (
        text
        .replace("«", '"').replace("»", '"')
        .replace("“", '"').replace("”", '"')
    )


def _extract_first_json(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char == '{':
            try:
                obj, _ = decoder.raw_decode(text, i)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    return None


def _repair_truncated_json(text: str) -> dict | None:
    """Extrae campos individuales de un JSON truncado/malformado via regex.
    Devuelve dict parcial si recupera al menos title o content."""
    result: dict = {}

    m = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        result["title"] = m.group(1).replace('\\"', '"')

    m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)', text)
    if m:
        content = m.group(1).replace('\\"', '"')
        last_p = content.rfind("</p>")
        if last_p > 0:
            content = content[:last_p + 4]
        elif content and not content.strip().startswith("<p>"):
            content = f"<p>{content.strip()}</p>"
        result["content"] = content

    m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        result["summary"] = m.group(1).replace('\\"', '"')

    m = re.search(r'"category"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        result["category"] = m.group(1).replace('\\"', '"')

    m = re.search(r'"keyphrase"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        result["keyphrase"] = m.group(1).replace('\\"', '"')

    if "title" in result or "content" in result:
        return result
    return None


def _normalize_summary(summary: str, title: str = "") -> str:
    """Devuelve UNA sola oración completa terminada en punto, máximo 30 palabras."""
    text = summary.strip()

    # Si hay varias oraciones, quedarse solo con la primera completa
    m = re.search(r'\.(?:\s|$)', text)
    if m:
        first = text[:m.start() + 1].strip()
        words = first.split()
        # Oración razonable: entre 8 y 30 palabras
        if 8 <= len(words) <= 30:
            return first
        # Demasiado larga: truncar en 28 palabras y cerrar con punto
        if len(words) > 30:
            return " ".join(words[:28]).rstrip(",;:") + "."

    # Sin punto claro: cortar en 25 palabras y completar si es muy corta
    words = text.split()
    if len(words) < 8:
        extra = [w for w in title.split() if w.lower() not in text.lower()]
        words = words + extra
    truncated = " ".join(words[:25]).rstrip(",;:")
    if not truncated.endswith("."):
        truncated += "."
    return truncated


def _clean_content(content: str) -> str:
    markers = [
        '\n«,', '\n",',
        '\n"category"', '\n«category»',
        '\n"summary"',  '\n«summary»',
        '\n"keyphrase"', '\n«keyphrase»',
        '\n"title"', '\n«title»',
        '\n"tags"', '\n«tags»',
    ]
    for m in markers:
        idx = content.find(m)
        if idx != -1:
            content = content[:idx]

    content = content.strip()

    if '«' in content or '»' in content:
        idx = content.find('«')
        content = content[:idx].strip() if idx > 0 else ''

    if content.startswith('{'):
        inner = _extract_first_json(_normalize_quotes(content))
        if inner and 'content' in inner:
            return _clean_content(inner['content'])
        return ''

    return content


_DEFAULT_CATEGORIES = (
    "Nacionales, Internacionales, Policiales, Política, Economía, "
    "Previsión Social, Salud, Educación, Tecnología, Deportes, "
    "Espectáculos, Cultura, Sociedad, Ciencia, Turismo, Medio Ambiente, General"
)

_CATEGORY_GUIDE = """CATEGORIZACIÓN — elegí la más específica entre las disponibles:
- Nacionales: hechos ocurridos en Argentina sin categoría más específica
- Internacionales / Mundo: noticias de otros países
- Policiales: ⚠ PRIORIDAD ALTA — usá esta categoría cuando haya: crímenes, robos, homicidios,
  detenidos, arrestos, allanamientos, narcotráfico, tráfico ilegal de medicamentos o drogas
  (fentanilo, cocaína, etc.), incautaciones, operativos de fuerzas de seguridad, violencia,
  accidentes fatales, contrabando, detenciones policiales. Si hay un delito o una fuerza de
  seguridad actuando, ES POLICIALES — aunque la sustancia sea un medicamento.
- Política: gobierno, elecciones, partidos, legisladores, decretos, actos de gobierno
- Economía: inflación, mercados, empresas, finanzas, dólar, precios, comercio exterior
- Previsión Social: ANSES, jubilaciones, pensiones, AUH, asignaciones familiares, seguridad social
- Salud: SOLO si no hay delito involucrado — enfermedades, hospitales, epidemias,
  campañas de vacunación, salud pública, uso legítimo de medicamentos
- Educación: escuelas, universidades, docentes, planes educativos, becas
- Tecnología: software, hardware, IA, telecomunicaciones, internet, innovación digital
- Deportes: fútbol, rugby, tenis, atletismo, cualquier competencia deportiva
- Espectáculos: cine, televisión, música, teatro, celebrities, series, farándula
- Cultura: arte, literatura, patrimonio, festivales culturales, museos
- Sociedad: temas sociales, comunidad, género, derechos humanos
- Ciencia: investigación científica, descubrimientos, astronomía, biología, física
- Turismo: viajes, destinos, hotelería, gastronomía, temporada turística
- Medio Ambiente: clima, ecología, catástrofes naturales, contaminación
- General: solo si ninguna otra categoría aplica claramente"""


def _call_anthropic_text(api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    """Llama a la API de Anthropic con el SDK nativo y devuelve el texto de respuesta."""
    import anthropic as _anthropic
    client = _anthropic.Anthropic(api_key=api_key)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if model != "claude-opus-4-7":
        kwargs["temperature"] = 0.85
    response = client.messages.create(**kwargs)
    return next(b.text for b in response.content if b.type == "text").strip()


def _detect_headings(text: str) -> bool:
    """True si el texto fuente contiene subtítulos HTML."""
    return bool(re.search(r'<h[2-4][^>]*>', text, re.IGNORECASE))


def _article_scale(char_count: int) -> tuple[str, int | None, int]:
    """Devuelve (rango_párrafos, palabras_mínimas_o_None, max_tokens) según largo de la fuente.
    Párrafos de 175-185 chars con 2 oraciones cada uno — min_words se eliminó porque
    contradice el límite de chars. La completitud se exige por prompt."""
    if char_count < 600:
        return "5 a 7", None, 3000
    elif char_count < 1500:
        return "8 a 12", None, 5000
    elif char_count < 3000:
        return "13 a 18", None, 7000
    elif char_count < 5000:
        return "18 a 24", None, 9000
    elif char_count < 8000:
        return "22 a 30", None, 11000
    else:
        return "28 a 38", None, 13000


def _chat_with_token_fallback(client, model: str, messages: list, max_tokens: int, **kwargs):
    """Llama a chat.completions.create y reintenta con menos tokens si OpenRouter devuelve 402."""
    try:
        return client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, **kwargs
        )
    except Exception as exc:
        err_str = str(exc)
        m = re.search(r'can only afford (\d+)', err_str)
        if m and '402' in err_str:
            affordable = int(m.group(1))
            log.warning("OpenRouter 402: reduciendo max_tokens %d → %d", max_tokens, affordable)
            return client.chat.completions.create(
                model=model, messages=messages, max_tokens=affordable, **kwargs
            )
        raise


def _merge_short_paragraphs(html: str, min_chars: int = 160, max_chars: int = 250) -> str:
    """Fusiona <p> consecutivos cortos hasta que cada párrafo tenga entre min_chars y max_chars caracteres."""
    import re as _re
    # Separar el HTML en tokens: <p>...</p> vs resto
    tokens = _re.split(r'(<p>.*?</p>)', html, flags=_re.DOTALL | _re.IGNORECASE)
    result: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def flush():
        if buf:
            result.append(f"<p>{' '.join(buf)}</p>")
            buf.clear()

    for tok in tokens:
        m = _re.match(r'<p>(.*?)</p>', tok, flags=_re.DOTALL | _re.IGNORECASE)
        if not m:
            flush()
            if tok:
                result.append(tok)
            continue
        inner = m.group(1).strip()
        # No tocar párrafos con HTML especial (subtítulos, listas, citas, etc.)
        if _re.search(r'<(?:h[1-6]|ul|ol|li|blockquote|table|strong|em)', inner, _re.IGNORECASE) and len(inner) > 60:
            flush()
            result.append(tok)
            continue
        # Párrafo ya en rango o más largo: volcar buffer, agregar tal cual
        if len(inner) >= min_chars:
            flush()
            result.append(tok)
            continue
        # Párrafo corto: intentar acumular
        extra = len(inner) + (1 if buf else 0)
        if buf and buf_len + extra > max_chars:
            flush()
        buf.append(inner)
        buf_len = buf_len + extra if buf_len else len(inner)

    flush()
    return ''.join(result)


def _split_long_paragraphs(html: str, max_chars: int = 183) -> str:
    """Divide <p> con más de max_chars caracteres en párrafos más cortos, cortando en oraciones.
    Cada <p> resultante tiene como máximo max_chars caracteres."""
    import re as _re
    def split_p(m):
        inner = m.group(1).strip()
        if len(inner) <= max_chars:
            return f"<p>{inner}</p>"
        # Partir en oraciones: ". ", "! ", "? " seguido de mayúscula o comilla
        sentences = _re.split(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡"“])', inner)
        if len(sentences) <= 1:
            return f"<p>{inner}</p>"
        # Agrupar respetando el límite: acumular oraciones hasta llegar a max_chars
        groups: list[str] = []
        current: list[str] = []
        current_len = 0
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            # Si agregar esta oración supera el límite, cerrar el grupo actual
            extra = len(sent) + (1 if current else 0)  # +1 por el espacio
            if current and current_len + extra > max_chars:
                groups.append(" ".join(current))
                current = [sent]
                current_len = len(sent)
            else:
                current.append(sent)
                current_len += extra
        if current:
            groups.append(" ".join(current))
        return "\n".join(f"<p>{g}</p>" for g in groups if g) or f"<p>{inner}</p>"

    return re.sub(r'<p>(.*?)</p>', split_p, html, flags=re.DOTALL | re.IGNORECASE)


def _add_paragraph_spacing(html: str) -> str:
    """Agrega margin-bottom inline a cada <p> sin estilo para garantizar separación visual."""
    import re as _re
    return _re.sub(r'<p(?!\s)', '<p style="margin-bottom:1.4em"', html)


def _text_to_html_paragraphs(text: str) -> str:
    """Convierte texto plano a párrafos HTML. Si ya tiene <p>, lo devuelve tal cual."""
    if "<p>" in text.lower():
        return text
    blocks = re.split(r'\n\s*\n', text.strip())
    parts = [f'<p>{b.strip().replace(chr(10), " ")}</p>' for b in blocks if b.strip()]
    if not parts:
        return f'<p>{text}</p>'
    if len(parts) == 1:
        words = text.split()
        if len(words) > 80:
            mid = len(words) // 2
            parts = [f'<p>{" ".join(words[:mid])}</p>', f'<p>{" ".join(words[mid:])}</p>']
    return '\n'.join(parts)


def generate_title_for_content(
    api_key: str,
    model: str,
    base_prompt: str,
    article_text: str,
    available_categories: list[str] | None = None,
    provider: str = "groq",
    api_base_url: str | None = None,
    title_hint: str = "",
) -> dict:
    """Genera título, bajada, categoría y etiquetas sin reescribir el contenido original."""
    cat_list = ", ".join(available_categories) if available_categories else _DEFAULT_CATEGORIES
    first_line = next(
        (l.strip() for l in article_text.splitlines() if len(l.strip()) > 20), ""
    )
    hint_line = (
        f"\nEl usuario identificó este contenido con el encabezado: «{title_hint}» — "
        "usalo como referencia para crear un título más potente y preciso.\n"
        if title_hint else ""
    )

    prompt = f"""Analizá el siguiente texto y generá un título periodístico potente para publicarlo en un medio digital argentino.
No reescribas el contenido. Solo generá el título, bajada, categoría y etiquetas.
{hint_line}
══════════════ TEXTO FUENTE ══════════════
{article_text[:8000]}
══════════════════════════════════════════

TÍTULO:
Creá un título periodístico atractivo, claro y con gancho para generar clics.
Debe reflejar el hecho más importante del texto.
Entre 80 y 110 caracteres. Sin punto al final. Sin comillas externas. Sin clickbait falso.

BAJADA:
Una sola oración completa que termina en punto. Máximo 25 palabras. Resumí el hecho principal: quién, qué y dónde.

{_CATEGORY_GUIDE}
Categorías disponibles: {cat_list}

Respondé ÚNICAMENTE con JSON válido. Sin markdown, sin texto extra.
{{
  "title": "Título entre 80 y 110 caracteres. Sin punto al final.",
  "summary": "Una sola oración terminada en punto. Máximo 25 palabras.",
  "category": "Exactamente una de estas: {cat_list}",
  "keyphrase": "frase clave 2-4 palabras",
  "tags": ["etiqueta1", "etiqueta2", "etiqueta3", "etiqueta4", "etiqueta5"]
}}"""

    max_tokens = 800
    if provider == "anthropic":
        raw = _call_anthropic_text(api_key, model, prompt, max_tokens)
    else:
        client = _get_client(api_key, provider, api_base_url)
        resp = _chat_with_token_fallback(
            client, model,
            [{"role": "user", "content": prompt}],
            max_tokens,
            temperature=0.7,
        )
        raw = resp.choices[0].message.content.strip()
    log.debug("AI title-only raw (300): %s", raw[:300])

    text = _normalize_quotes(raw)
    result = _extract_first_json(text)
    if result:
        if "title" in result:
            result["title"] = _clean_subject(result["title"])
        if "summary" in result:
            result["summary"] = _normalize_summary(result["summary"], result.get("title", ""))
        return result

    log.warning("generate_title_for_content: no se pudo parsear JSON. Raw: %s", raw[:200])
    return {
        "title": first_line[:80] if first_line else article_text[:60],
        "summary": article_text[:200],
        "category": "General",
        "keyphrase": "",
        "tags": [],
    }


def process_rss_with_groq(
    api_key: str,
    model: str,
    base_prompt: str,
    title: str,
    article_text: str,
    available_categories: list[str] | None = None,
    provider: str = "groq",
    api_base_url: str | None = None,
) -> dict:
    cat_list = ", ".join(available_categories) if available_categories else _DEFAULT_CATEGORIES

    source_len = len(article_text)
    para_range, _min_words, max_tokens = _article_scale(source_len)
    source_has_headings = _detect_headings(article_text)

    if source_has_headings:
        heading_rule = "Usá 1 o 2 <h2> para separar secciones temáticas distintas si el contenido lo justifica."
        content_hint = "<p> bien separados, con <h2> donde corresponda"
    else:
        heading_rule = "NO uses <h2> ni ningún subtítulo. El artículo fuente no tiene subtítulos."
        content_hint = "<p> bien separados, sin subtítulos"

    word_count_rule = "Cubrí TODA la información útil del contenido fuente. No omitas datos, protagonistas ni detalles relevantes. Usá los párrafos que sean necesarios para no dejar nada afuera."

    para_size_rule = (
        "Cada <p> DEBE tener SIEMPRE 2 oraciones (excepcionalmente 3 si son muy breves). "
        "NUNCA 1 sola oración por <p>. "
        "Cada oración debe ser corta: entre 70 y 100 caracteres. "
        "El <p> completo debe totalizar entre 175 y 185 caracteres con espacios. "
        "Si una oración sola supera los 175 caracteres, ese <p> se acepta como excepción, pero es el único caso."
    )

    prompt = f"""{base_prompt}

══════════════ ARTÍCULO FUENTE — REESCRIBÍ SOLO ESTO ══════════════
{article_text[:20000]}
═══════════════════════════════════════════════════════════════════

⚠ SOBRE EL CONTENIDO RECIBIDO: El texto anterior proviene de un scraping web y puede contener elementos de interfaz del sitio que NO son parte de la noticia: bylines de autor con separadores (•), widgets en inglés ("Comments are closed", "Public collection", "Private collection", "Here you'll find..."), menús, botones o etiquetas de categoría. IGNORÁ completamente esos elementos. Procesá ÚNICAMENTE el cuerpo periodístico de la noticia.

OBJETIVO PRINCIPAL:
No hagas un resumen corto. Convertí la información recibida en una nota periodística bien redactada, con enfoque digital, pensada para que el lector haga clic, entre a leer y se mantenga interesado, sin caer en plagio, sensacionalismo falso ni datos inventados.

REGLAS FUNDAMENTALES:
- Usá toda la información útil del contenido recibido.
- PROHIBIDO copiar texto del original. Reescribí TODA la nota con palabras propias. Ninguna oración puede coincidir con el texto fuente, salvo citas textuales entre comillas con atribución explícita.
- No inventes datos, nombres, cifras, causas, fechas ni consecuencias.
- No agregues información que no esté en el contenido.
- No repitas ideas innecesariamente.
- No hagas párrafos largos.
- No uses lenguaje robótico.
- No escribas como comunicado institucional, salvo que el contenido lo requiera.
- La noticia debe sonar natural, profesional y humana.
- UBICACIÓN GEOGRÁFICA (OBLIGATORIO): nombrar siempre ciudad, provincia, barrio o localidad.
  Si el texto dice "Posadas" → escribís "Posadas". Si dice "Misiones" → escribís "Misiones".
  NUNCA reemplaces por "una ciudad", "la provincia" o "la zona".

ESTILO PERIODÍSTICO:
- Voz activa.
- Frases claras, directas y concretas.
- Párrafos cortos de 2 a 4 líneas como máximo.
- Lectura ágil, con alta legibilidad.
- Separación clara entre ideas.
- Tono informativo, pero con gancho.
- Priorizá siempre el hecho principal desde el primer párrafo: qué pasó, quiénes participaron, cuándo, dónde y por qué importa.

ENFOQUE SEGÚN EL TIPO DE NOTICIA:
Adaptá el tono según el tema recibido:

1. Policiales:
Usá un tono firme, directo y con tensión informativa, sin morbo ni exageraciones. Destacá el hecho, el lugar, los involucrados, el operativo, las consecuencias y los datos que generen interés público.

2. Política:
Usá un tono que invite al debate, mostrando el conflicto, la decisión, las posturas, el impacto o la discusión pública. No tomes partido. Presentá los hechos de forma equilibrada.

3. Sociedad:
Dale un enfoque humano y cercano. Resaltá cómo afecta a vecinos, familias, instituciones o la comunidad.

4. Economía:
Enfocá la noticia en el impacto concreto: bolsillo, precios, empleo, comercio, producción, beneficios o perjuicios.

5. Clima o alertas:
Usá un tono preventivo y claro. Destacá zonas afectadas, horarios, riesgos y recomendaciones si están en el contenido.

6. Deportes:
Usá un tono dinámico, con emoción y contexto competitivo, sin exagerar resultados o hechos que no estén confirmados.

7. Municipales o institucionales:
Redactá de forma profesional y cercana, evitando que parezca una gacetilla. Resaltá el impacto real para la comunidad.

TÍTULO:
Creá un título periodístico atractivo, claro y con gancho.
Debe generar interés para hacer clic, pero sin mentir ni exagerar.
Evitá títulos planos o genéricos.
No uses clickbait falso.
El título debe reflejar el hecho más fuerte de la noticia.
LONGITUD OBLIGATORIA: entre 80 y 110 caracteres. Sin punto al final. Sin comillas externas.
Referencia del título original (NO copies literalmente): {title}

BAJADA:
Escribí una bajada breve de 1 línea.
Debe ampliar el título y resumir lo más importante de la noticia, dejando motivo para seguir leyendo.
UNA sola oración completa que termina en punto. Máximo 25 palabras.

CUERPO DE LA NOTA:
- Primer párrafo: contar el hecho principal de manera fuerte y clara.
- Segundo párrafo: sumar datos importantes, contexto o protagonistas.
- Desarrollo: ordenar la información de mayor a menor importancia.
- Incluir detalles relevantes del contenido recibido.
- Si hay declaraciones textuales importantes, usarlas entre comillas con atribución clara.
- Si hay conflicto, debate o impacto social, destacarlo de manera objetiva.
- Cierre: terminar con un dato relevante, una consecuencia, una continuidad del caso o el contexto disponible.

FORMATO HTML OBLIGATORIO DEL CUERPO:
Cada párrafo DEBE estar entre <p> y </p>. SIN EXCEPCIÓN.
⚠ REGLA DE ORO — PÁRRAFOS:
  • {para_size_rule}
  • Ejemplo CORRECTO (2 oraciones cortas, total ~180 chars):
      <p>Un cuerpo fue hallado el miércoles en el río Paraná, en el distrito de Presidente Franco. La víctima, un hombre sin identificar, presentaba un avanzado estado de descomposición.</p>
      <p>El Ministerio Público ordenó derivar los restos a una funeraria local para las pericias correspondientes. No se encontró documentación junto al cuerpo.</p>
  • Ejemplo INCORRECTO (1 sola oración = RECHAZADO por ser menor a 160 chars):
      <p>Un cuerpo fue hallado en el río Paraná, en el distrito de Presidente Franco.</p>
      <p>La víctima era un hombre sin identificar.</p>
{para_range} párrafos en total.
SUBTÍTULOS: {heading_rule}
PROHIBIDO: <ul>, <ol>, listas de cualquier tipo, más de 2 usos de <strong>, texto fuera de <p>.

ORIGINALIDAD:
Reescribí completamente la noticia con palabras propias.
Podés cambiar el orden de la información para mejorar la lectura.
Mantené los datos reales, pero evitá que el texto parezca copiado del medio original.

EXTENSIÓN:
- {word_count_rule}
- Desarrollá cada punto con profundidad periodística: explicá el contexto, el impacto, el porqué importa, las consecuencias o los antecedentes que ayuden al lector a entender mejor la noticia.
- Podés agregar oraciones que contextualicen o amplíen cada hecho, siempre que sean coherentes con la información recibida. No inventés datos específicos (nombres, cifras, fechas) que no estén en el contenido.
- No termines la nota de forma abrupta.

LEGIBILIDAD:
- Cada <p> DEBE tener entre 160 y 183 caracteres con espacios. Acumulá oraciones en el mismo <p> hasta alcanzar ese rango. Solo abrís un nuevo <p> cuando agregar otra oración superaría los 183 chars.
- Si una oración sola supera 183 chars, ese es el párrafo completo.
- Oraciones claras y directas, de 15 a 25 palabras.
- No uses palabras difíciles si no son necesarias.
- La lectura debe ser simple, fluida y entendible para cualquier lector.

INTERACCIÓN Y CLICS:
La nota debe despertar interés real.
Buscá el ángulo más fuerte de la información:
conflicto, impacto, sorpresa, consecuencia, cercanía con la comunidad, debate público, alerta, dato relevante.
Pero siempre respetando la verdad del contenido recibido.

{_CATEGORY_GUIDE}
Categorías disponibles: {cat_list}

ANTES DE ENTREGAR — Verificá internamente que:
- No haya datos inventados.
- No haya plagio.
- Se haya usado toda la información relevante.
- El título tenga gancho real y entre 80 y 110 caracteres.
- Cada <p> tenga entre 175 y 185 caracteres (2 oraciones cortas). Si alguno tiene 1 sola oración y menos de 160 chars, fusionalo con el siguiente. Si supera 185, divídilo.
- La nota no sea un resumen.
- El texto esté listo para publicar en un medio digital argentino.
- La ubicación geográfica (ciudad, provincia) esté nombrada explícitamente.

WA_DATA — SOLO para contenido con datos estructurados (ANSES, previsión social, calendarios, listas de montos):
- Si el contenido tiene un calendario de fechas de pago tipo "DNI terminado en X cobra el día Y" o similares:
  generá en `wa_data` una tabla de texto plano con emoji 📅, encabezado descriptivo y separadores ───────────.
  Ejemplo:
  📅 CALENDARIO DE PAGOS - [MES AÑO]
  ───────────────────────────────
  DNI final 0 y 1  →  lunes X
  DNI final 2 y 3  →  martes X
  ...
  ───────────────────────────────
- Si el contenido tiene una lista de montos, haberes o beneficios específicos tipo "AUH: $X / Jubilación mínima: $Y":
  generá en `wa_data` una lista con bullets • y emoji 💰, con encabezado descriptivo.
  Ejemplo:
  💰 NUEVOS MONTOS - [MES AÑO]
  ───────────────────────────────
  • Jubilación mínima: $X.XXX
  • AUH: $X.XXX
  ───────────────────────────────
- En CUALQUIER OTRO CASO (noticias sin datos tabulares), dejá `wa_data` como cadena vacía "".

IMPORTANTE: Respondé ÚNICAMENTE con JSON válido. Sin markdown, sin texto extra.
Las comillas dentro del HTML van con barra invertida \" o como &quot;. Atributos HTML con comillas simples.
{{
  "title": "Título periodístico atractivo entre 80 y 110 caracteres. Con gancho. Sin punto al final.",
  "content": "<p>Oración 1 corta (70-100 chars). Oración 2 corta (70-100 chars). Total ~175-185 chars.</p><p>Oración 1. Oración 2.</p> — CONTINUAR ASÍ {para_range} párrafos. {word_count_rule} CADA <p> SIEMPRE 2 ORACIONES, TOTAL 175-185 CHARS.",
  "category": "Exactamente una de estas: {cat_list}",
  "summary": "UNA sola oración completa que termina en punto. Máximo 25 palabras. El hecho principal: quién, qué y dónde. Sin segunda oración.",
  "keyphrase": "frase clave 2-4 palabras",
  "tags": ["etiqueta1", "etiqueta2", "etiqueta3", "etiqueta4", "etiqueta5"],
  "wa_data": "Tabla o lista de texto plano para WhatsApp si es calendario/lista ANSES; cadena vacía si no aplica."
}}"""

    if provider == "anthropic":
        raw = _call_anthropic_text(api_key, model, prompt, max_tokens)
    else:
        client = _get_client(api_key, provider, api_base_url)
        resp = _chat_with_token_fallback(
            client, model,
            [{"role": "user", "content": prompt}],
            max_tokens,
            temperature=0.85,
        )
        raw = resp.choices[0].message.content.strip()
    log.debug("AI RSS raw response (500 chars): %s", raw[:500])

    text = _normalize_quotes(raw)
    result = _extract_first_json(text)

    if result:
        if "title" in result:
            result["title"] = _clean_subject(result["title"])
        if "content" in result:
            result["content"] = _clean_content(result["content"])
            if not result["content"]:
                log.warning("Content RSS vacío tras limpieza")
                result["content"] = f"<p>{article_text[:1000]}</p>"
            result["content"] = _merge_short_paragraphs(result["content"])
            result["content"] = _split_long_paragraphs(result["content"])
            result["content"] = _add_paragraph_spacing(result["content"])
        if "summary" in result:
            result["summary"] = _normalize_summary(result["summary"], result.get("title", title))
        return result

    log.warning("No se pudo parsear JSON RSS. Raw (200): %s", raw[:200])

    # Intento de reparación: extraer campos de JSON truncado
    repaired = _repair_truncated_json(text)
    if repaired and repaired.get("content"):
        log.info("JSON RSS reparado parcialmente: title=%s, content=%d chars",
                 bool(repaired.get("title")), len(repaired.get("content", "")))
        if "title" in repaired:
            repaired["title"] = _clean_subject(repaired["title"])
        if "summary" in repaired:
            repaired["summary"] = _normalize_summary(repaired["summary"], repaired.get("title", title))
        repaired.setdefault("title", title)
        repaired.setdefault("category", "General")
        return repaired

    fallback_title = title
    if not fallback_title or len(fallback_title) < 10:
        fallback_title = next(
            (l.strip() for l in article_text.splitlines() if len(l.strip()) > 25),
            "Noticia"
        )
    return {
        "title": fallback_title,
        "content": f"<p>{article_text[:1000]}</p>",
        "category": "General",
        "summary": article_text[:200],
    }


def process_email_with_groq(
    api_key: str,
    model: str,
    base_prompt: str,
    subject: str,
    body: str,
    available_categories: list[str] | None = None,
    provider: str = "groq",
    api_base_url: str | None = None,
) -> dict:
    clean_subject = _clean_subject(subject)
    cat_list = ", ".join(available_categories) if available_categories else _DEFAULT_CATEGORIES

    body_len = len(body)
    para_range, _min_words, max_tokens = _article_scale(body_len)

    word_count_rule = "Cubrí TODA la información útil del contenido fuente. No omitas datos, protagonistas ni detalles relevantes. Usá los párrafos que sean necesarios para no dejar nada afuera."

    para_size_rule = (
        "Cada <p> DEBE tener SIEMPRE 2 oraciones (excepcionalmente 3 si son muy breves). "
        "NUNCA 1 sola oración por <p>. "
        "Cada oración debe ser corta: entre 70 y 100 caracteres. "
        "El <p> completo debe totalizar entre 175 y 185 caracteres con espacios. "
        "Si una oración sola supera los 175 caracteres, ese <p> se acepta como excepción, pero es el único caso."
    )

    # Extraer la primera línea significativa del cuerpo como pista de titular
    first_body_line = next(
        (l.strip() for l in body.splitlines() if len(l.strip()) > 15),
        ""
    )

    prompt = f"""{base_prompt}

══════════════ CONTENIDO FUENTE — REESCRIBÍ SOLO ESTO ══════════════
{body[:20000]}
════════════════════════════════════════════════════════════════════

OBJETIVO PRINCIPAL:
No hagas un resumen corto. Convertí la información recibida en una nota periodística bien redactada, con enfoque digital, pensada para que el lector haga clic, entre a leer y se mantenga interesado, sin caer en plagio, sensacionalismo falso ni datos inventados.

REGLAS FUNDAMENTALES:
- Usá toda la información útil del contenido recibido.
- No copies frases completas del texto original, salvo declaraciones textuales entre comillas.
- No inventes datos, nombres, cifras, causas, fechas ni consecuencias.
- No agregues información que no esté en el contenido.
- No repitas ideas innecesariamente.
- No hagas párrafos largos.
- No uses lenguaje robótico.
- No escribas como comunicado institucional, salvo que el contenido lo requiera.
- La noticia debe sonar natural, profesional y humana.
- UBICACIÓN GEOGRÁFICA (OBLIGATORIO): nombrar siempre ciudad, provincia, barrio o localidad.
  Si el texto dice "Posadas" → escribís "Posadas". Si dice "Misiones" → escribís "Misiones".
  NUNCA reemplaces por "una ciudad", "la provincia" o "la zona".
El hecho principal es: "{first_body_line[:120]}"

ESTILO PERIODÍSTICO:
- Voz activa.
- Frases claras, directas y concretas.
- Párrafos cortos de 2 a 4 líneas como máximo.
- Lectura ágil, con alta legibilidad.
- Separación clara entre ideas.
- Tono informativo, pero con gancho.
- Priorizá siempre el hecho principal desde el primer párrafo: qué pasó, quiénes participaron, cuándo, dónde y por qué importa.

ENFOQUE SEGÚN EL TIPO DE NOTICIA:
Adaptá el tono según el tema recibido:

1. Policiales:
Usá un tono firme, directo y con tensión informativa, sin morbo ni exageraciones. Destacá el hecho, el lugar, los involucrados, el operativo, las consecuencias y los datos que generen interés público.

2. Política:
Usá un tono que invite al debate, mostrando el conflicto, la decisión, las posturas, el impacto o la discusión pública. No tomes partido. Presentá los hechos de forma equilibrada.

3. Sociedad:
Dale un enfoque humano y cercano. Resaltá cómo afecta a vecinos, familias, instituciones o la comunidad.

4. Economía:
Enfocá la noticia en el impacto concreto: bolsillo, precios, empleo, comercio, producción, beneficios o perjuicios.

5. Clima o alertas:
Usá un tono preventivo y claro. Destacá zonas afectadas, horarios, riesgos y recomendaciones si están en el contenido.

6. Deportes:
Usá un tono dinámico, con emoción y contexto competitivo, sin exagerar resultados o hechos que no estén confirmados.

7. Municipales o institucionales:
Redactá de forma profesional y cercana, evitando que parezca una gacetilla. Resaltá el impacto real para la comunidad.

TÍTULO:
Creá un título periodístico atractivo, claro y con gancho.
Debe generar interés para hacer clic, pero sin mentir ni exagerar.
Evitá títulos planos o genéricos.
No uses clickbait falso.
El título debe reflejar el hecho más fuerte de la noticia.
LONGITUD OBLIGATORIA: entre 80 y 110 caracteres. Sin punto al final. Sin comillas externas.

BAJADA:
Escribí una bajada breve de 1 línea.
Debe ampliar el título y resumir lo más importante de la noticia, dejando motivo para seguir leyendo.
UNA sola oración completa que termina en punto. Máximo 25 palabras.

CUERPO DE LA NOTA:
- Primer párrafo: contar el hecho principal de manera fuerte y clara.
- Segundo párrafo: sumar datos importantes, contexto o protagonistas.
- Desarrollo: ordenar la información de mayor a menor importancia.
- Incluir detalles relevantes del contenido recibido.
- Si hay declaraciones textuales importantes, usarlas entre comillas con atribución clara.
- Si hay conflicto, debate o impacto social, destacarlo de manera objetiva.
- Cierre: terminar con un dato relevante, una consecuencia, una continuidad del caso o el contexto disponible.

FORMATO HTML OBLIGATORIO DEL CUERPO:
Cada párrafo DEBE estar entre <p> y </p>. SIN EXCEPCIÓN.
⚠ REGLA DE ORO — PÁRRAFOS CORTOS:
  • {para_size_rule}
  • Ejemplo CORRECTO (2 oraciones cortas, total ~180 chars):
      <p>Un cuerpo fue hallado el miércoles en el río Paraná, en el distrito de Presidente Franco. La víctima, un hombre sin identificar, presentaba un avanzado estado de descomposición.</p>
      <p>El Ministerio Público ordenó derivar los restos a una funeraria local para las pericias correspondientes. No se encontró documentación junto al cuerpo.</p>
  • Ejemplo INCORRECTO (1 sola oración = RECHAZADO por ser menor a 160 chars):
      <p>Un cuerpo fue hallado en el río Paraná, en el distrito de Presidente Franco.</p>
      <p>La víctima era un hombre sin identificar.</p>
{para_range} párrafos en total.
Si el contenido tiene secciones claramente diferenciadas, podés usar <h2> para separar. Máximo 2.
PROHIBIDO: <ul>, <ol>, listas de cualquier tipo, más de 2 usos de <strong>, texto fuera de <p>.

ORIGINALIDAD:
Reescribí completamente la noticia con palabras propias.
Podés cambiar el orden de la información para mejorar la lectura.
Mantené los datos reales, pero evitá que el texto parezca copiado del medio original.

EXTENSIÓN:
- {word_count_rule}
- Desarrollá cada punto con profundidad periodística: explicá el contexto, el impacto, el porqué importa, las consecuencias o los antecedentes que ayuden al lector a entender mejor la noticia.
- Podés agregar oraciones que contextualicen o amplíen cada hecho, siempre que sean coherentes con la información recibida. No inventés datos específicos (nombres, cifras, fechas) que no estén en el contenido.
- No termines la nota de forma abrupta.

LEGIBILIDAD:
- Cada <p> DEBE tener entre 160 y 183 caracteres con espacios. Acumulá oraciones en el mismo <p> hasta alcanzar ese rango. Solo abrís un nuevo <p> cuando agregar otra oración superaría los 183 chars.
- Si una oración sola supera 183 chars, ese es el párrafo completo.
- Oraciones claras y directas, de 15 a 25 palabras.
- No uses palabras difíciles si no son necesarias.
- La lectura debe ser simple, fluida y entendible para cualquier lector.

INTERACCIÓN Y CLICS:
La nota debe despertar interés real.
Buscá el ángulo más fuerte de la información:
conflicto, impacto, sorpresa, consecuencia, cercanía con la comunidad, debate público, alerta, dato relevante.
Pero siempre respetando la verdad del contenido recibido.

{_CATEGORY_GUIDE}
Categorías disponibles: {cat_list}

ANTES DE ENTREGAR — Verificá internamente que:
- No haya datos inventados.
- No haya plagio.
- Se haya usado toda la información relevante.
- El título tenga gancho real y entre 80 y 110 caracteres.
- Cada <p> tenga entre 175 y 185 caracteres (2 oraciones cortas). Si alguno tiene 1 sola oración y menos de 160 chars, fusionalo con el siguiente. Si supera 185, divídilo.
- La nota no sea un resumen.
- El texto esté listo para publicar en un medio digital argentino.
- La ubicación geográfica (ciudad, provincia) esté nombrada explícitamente.

WA_DATA — SOLO para contenido con datos estructurados (ANSES, previsión social, calendarios, listas de montos):
- Si el contenido tiene un calendario de fechas de pago tipo "DNI terminado en X cobra el día Y" o similares:
  generá en `wa_data` una tabla de texto plano con emoji 📅, encabezado descriptivo y separadores ───────────.
  Ejemplo:
  📅 CALENDARIO DE PAGOS - [MES AÑO]
  ───────────────────────────────
  DNI final 0 y 1  →  lunes X
  DNI final 2 y 3  →  martes X
  ...
  ───────────────────────────────
- Si el contenido tiene una lista de montos, haberes o beneficios específicos tipo "AUH: $X / Jubilación mínima: $Y":
  generá en `wa_data` una lista con bullets • y emoji 💰, con encabezado descriptivo.
  Ejemplo:
  💰 NUEVOS MONTOS - [MES AÑO]
  ───────────────────────────────
  • Jubilación mínima: $X.XXX
  • AUH: $X.XXX
  ───────────────────────────────
- En CUALQUIER OTRO CASO (noticias sin datos tabulares), dejá `wa_data` como cadena vacía "".

IMPORTANTE: Respondé ÚNICAMENTE con JSON válido. Sin markdown, sin texto extra.
Comillas dobles estándar. Comillas SIMPLES dentro del HTML para atributos.
{{
  "title": "Título periodístico atractivo entre 80 y 110 caracteres. Con gancho. Sin punto al final.",
  "content": "<p>Oración 1 corta (70-100 chars). Oración 2 corta (70-100 chars). Total ~175-185 chars.</p><p>Oración 1. Oración 2.</p> — CONTINUAR ASÍ {para_range} párrafos. {word_count_rule} CADA <p> SIEMPRE 2 ORACIONES, TOTAL 175-185 CHARS.",
  "category": "Exactamente una de estas opciones, sin modificar el nombre: {cat_list}",
  "summary": "UNA sola oración completa que termina en punto. Máximo 25 palabras. El hecho principal: quién, qué y dónde. Sin segunda oración.",
  "keyphrase": "frase clave de 2 a 4 palabras",
  "tags": ["etiqueta1", "etiqueta2", "etiqueta3", "etiqueta4", "etiqueta5"],
  "wa_data": "Tabla o lista de texto plano para WhatsApp si es calendario/lista ANSES; cadena vacía si no aplica."
}}"""

    if provider == "anthropic":
        raw = _call_anthropic_text(api_key, model, prompt, max_tokens)
    else:
        client = _get_client(api_key, provider, api_base_url)
        resp = _chat_with_token_fallback(
            client, model,
            [{"role": "user", "content": prompt}],
            max_tokens,
            temperature=0.85,
        )
        raw = resp.choices[0].message.content.strip()
    log.debug("AI raw response (500 chars): %s", raw[:500])

    text = _normalize_quotes(raw)
    result = _extract_first_json(text)

    if result:
        if "title" in result:
            result["title"] = _clean_subject(result["title"])
        if "content" in result:
            result["content"] = _clean_content(result["content"])
            if not result["content"]:
                log.warning("Content vacío tras limpieza, usando cuerpo original")
                result["content"] = f"<p>{body[:1000]}</p>"
            result["content"] = _merge_short_paragraphs(result["content"])
            result["content"] = _split_long_paragraphs(result["content"])
            result["content"] = _add_paragraph_spacing(result["content"])
        if "summary" in result:
            result["summary"] = _normalize_summary(result["summary"], result.get("title", clean_subject))
        return result

    log.warning("No se pudo parsear JSON de IA. Raw (200): %s", raw[:200])

    repaired = _repair_truncated_json(text)
    if repaired and repaired.get("content"):
        log.info("JSON email reparado parcialmente: title=%s, content=%d chars",
                 bool(repaired.get("title")), len(repaired.get("content", "")))
        if "title" in repaired:
            repaired["title"] = _clean_subject(repaired["title"])
        if "summary" in repaired:
            repaired["summary"] = _normalize_summary(repaired["summary"], repaired.get("title", clean_subject))
        repaired.setdefault("title", first_body_line[:65] if first_body_line else clean_subject)
        repaired.setdefault("category", "General")
        return repaired

    fallback_title = first_body_line[:65] if first_body_line else clean_subject
    return {
        "title": fallback_title,
        "content": f"<p>{body[:1000]}</p>",
        "category": "General",
        "summary": body[:200],
    }
