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
            "qwen/qwen-2.5-72b-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "deepseek/deepseek-chat-v3-0324",
            "meta-llama/llama-3.3-70b-instruct",
            "qwen/qwen-2.5-72b-instruct",
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
        "Describí esta imagen en español de forma concisa y directa. "
        "Si contiene texto (captura de pantalla, imagen de noticia, cartel, documento, tweet), "
        "transcribí el texto completo y exacto. "
        "Si no hay texto, describí el acontecimiento o escena principal."
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


def _normalize_summary(summary: str, title: str = "") -> str:
    words = summary.strip().split()
    if len(words) >= 18:
        return " ".join(words[:22])
    extra = [w for w in title.split() if w.lower() not in summary.lower()]
    words = words + extra
    return " ".join(words[:20])


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
- Policiales: crímenes, robos, homicidios, detenidos, narcotráfico, violencia, accidentes fatales
- Política: gobierno, elecciones, partidos, legisladores, decretos, actos de gobierno
- Economía: inflación, mercados, empresas, finanzas, dólar, precios, comercio exterior
- Previsión Social: ANSES, jubilaciones, pensiones, AUH, asignaciones familiares, seguridad social
- Salud: enfermedades, hospitales, medicamentos, epidemias, salud pública
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


def _article_scale(char_count: int) -> tuple[str, int, int]:
    """Devuelve (rango_párrafos, palabras_mínimas, max_tokens) según largo de la fuente."""
    if char_count < 1500:
        return "4 a 5", 400, 5000
    elif char_count < 3000:
        return "5 a 7", 550, 6000
    elif char_count < 5000:
        return "7 a 9", 700, 7000
    else:
        return "9 a 12", 900, 8000


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
    para_range, min_words, max_tokens = _article_scale(source_len)
    source_has_headings = _detect_headings(article_text)

    if source_has_headings:
        heading_rule = "Usá 1 o 2 <h2> para separar secciones temáticas distintas si el contenido lo justifica."
        content_hint = f"<p> bien separados, con <h2> donde corresponda"
    else:
        heading_rule = "NO uses <h2> ni ningún subtítulo. El artículo fuente no tiene subtítulos."
        content_hint = "<p> bien separados, sin subtítulos"

    prompt = f"""{base_prompt}

╔══════════════════════════════════════════════════════╗
║  REGLA #1 — TÍTULO VIRAL  (leer antes que todo)     ║
╚══════════════════════════════════════════════════════╝
El título es lo más importante de la nota. Debe tener ENTRE 80 Y 110 CARACTERES.
Inspirate en Infobae, Clarín y La Nación. Ejemplos reales del estilo requerido:
• "Un comerciante brasileño cruzó la frontera, comparó los precios y no podía creer lo que veía"
• "Reveló que los precios en Argentina son 50% más baratos que en Brasil y el video se hizo viral"
• "¿Por qué cientos de brasileños cruzan la frontera solo para hacer las compras en Argentina?"
• "\"En Brasil vale el doble\": el comerciante que viajó y volvió con el changuito desbordado"
• "Fue a inaugurar una obra pública, encontró irregularidades graves y el Gobierno ya tiene que responder"
• "Confirmaron los 40 despidos en la planta de Córdoba: los trabajadores cortaron la ruta principal"
CONTÁ los caracteres antes de responder. Si tu título tiene menos de 75 caracteres, ES INCORRECTO — rehacelo más largo y específico. Sin punto al final. Sin comillas que envuelvan todo el título.

Título original (solo referencia, NO copies): {title}

Contenido del artículo:
{article_text[:8000]}

INSTRUCCIONES DE REDACCIÓN:

Sos un periodista argentino con 20 años de experiencia en diarios nacionales. Escribís con voz activa, frases directas y datos concretos. El texto debe pasar cualquier detector de IA como escrito por humano.

LEGIBILIDAD: oraciones de entre 10 y 18 palabras. Vocabulario cotidiano, sin tecnicismos. Alternás oraciones cortas con algunas más largas para ritmo natural. Apuntás a 95% de legibilidad Flesch.

PÁRRAFOS: {para_range} párrafos en total. Cada <p> contiene UNA sola idea concreta, con MÁXIMO 2 oraciones. Párrafos cortos, directos y bien separados entre sí. NUNCA más de 2 oraciones por párrafo.
- Primer párrafo: quién, qué, cuándo, dónde en 2 oraciones directas y fuertes.
- Párrafos del medio: contexto, antecedentes, declaraciones. Una cita textual clave va entre comillas con <strong> solo en la frase citada.
- Último párrafo: consecuencia, dato de cierre o proyección. Sin anunciar que termina.

SUBTÍTULOS: {heading_rule}

PROHIBIDO:
- <ul>, <ol> ni listas de ningún tipo
- "En conclusión", "En resumen", "En definitiva", "Para finalizar"
- "En primer lugar", "A continuación", "Por otro lado", "Cabe destacar"
- Mencionar fuente, medio original, URLs ni sitios externos
- "Fuente:", "Según informó...", "El portal X indicó que..."
- Más de 2 usos de <strong>

{_CATEGORY_GUIDE}
Categorías disponibles: {cat_list}

IMPORTANTE: Responde ÚNICAMENTE con JSON válido. Sin markdown, sin texto extra.
Comillas dobles estándar. Comillas SIMPLES dentro del HTML para atributos.
{{
  "title": "Título VIRAL entre 80 y 110 caracteres, estilo Infobae/Clarín. NUNCA menos de 75 caracteres. Con nombre, cifra o detalle concreto.",
  "content": "HTML periodístico. {para_range} párrafos {content_hint}. Cada <p> máximo 2 oraciones. Mínimo {min_words} palabras. Sin listas.",
  "category": "Exactamente una de estas opciones, sin modificar el nombre: {cat_list}",
  "summary": "EXACTAMENTE 20 palabras — ni una más ni una menos. Contá las palabras antes de responder. Genera curiosidad e incluye la palabra clave.",
  "keyphrase": "frase clave de 2 a 4 palabras",
  "tags": ["etiqueta1", "etiqueta2", "etiqueta3", "etiqueta4", "etiqueta5"]
}}"""

    if provider == "anthropic":
        raw = _call_anthropic_text(api_key, model, prompt, max_tokens)
    else:
        client = _get_client(api_key, provider, api_base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
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
        if "summary" in result:
            result["summary"] = _normalize_summary(result["summary"], result.get("title", title))
        return result

    log.warning("No se pudo parsear JSON RSS. Raw (200): %s", raw[:200])
    # Fallback: usar primera línea larga del artículo como título (no el hint vacío o de dominio)
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
    para_range, min_words, max_tokens = _article_scale(body_len)

    # Extraer la primera línea significativa del cuerpo como pista de titular
    first_body_line = next(
        (l.strip() for l in body.splitlines() if len(l.strip()) > 15),
        ""
    )

    prompt = f"""{base_prompt}

╔══════════════════════════════════════════════════════╗
║  REGLA #1 — TÍTULO VIRAL  (leer antes que todo)     ║
╚══════════════════════════════════════════════════════╝
El título es lo más importante de la nota. Debe tener ENTRE 80 Y 110 CARACTERES.
Inspirate en Infobae, Clarín y La Nación. Ejemplos reales del estilo requerido:
• "Un comerciante brasileño cruzó la frontera, comparó los precios y no podía creer lo que veía"
• "Reveló que los precios en Argentina son 50% más baratos que en Brasil y el video se hizo viral"
• "¿Por qué cientos de brasileños cruzan la frontera solo para hacer las compras en Argentina?"
• "Confirmaron los 40 despidos en la planta de Córdoba: los trabajadores cortaron la ruta principal"
• "Fue a inaugurar una obra pública, encontró irregularidades graves y el Gobierno ya tiene que responder"
• "Sube 20% la tarifa de luz en todo el país: cuánto vas a pagar de más a partir del mes que viene"
El hecho principal del contenido es: "{first_body_line[:80]}" — reescribilo con ese estilo viral.
CONTÁ los caracteres antes de responder. Si tu título tiene menos de 75 caracteres, ES INCORRECTO — rehacelo más largo. NUNCA copies el asunto del correo. Sin punto al final.

CONTENIDO A TRANSFORMAR EN NOTICIA:
{body[:8000]}

INSTRUCCIONES DE REDACCIÓN:

Sos un periodista argentino con 20 años de experiencia en diarios nacionales. Tu tarea es REESCRIBIR completamente el contenido anterior como una noticia periodística. NO copies frases textuales — reformulá cada idea con voz propia, activa y directa. El texto debe sonar 100% humano.

LEGIBILIDAD: oraciones de entre 10 y 18 palabras. Vocabulario cotidiano. Alternás oraciones cortas con largas para ritmo natural. Apuntás a 95% de legibilidad Flesch.

PÁRRAFOS: {para_range} párrafos en total. Cada <p> con UNA sola idea, MÁXIMO 2 oraciones.
- Primer párrafo: quién, qué, cuándo, dónde — 2 oraciones directas y fuertes.
- Párrafos del medio: contexto, antecedentes, declaraciones textuales entre comillas con <strong>.
- Último párrafo: consecuencia o proyección. Sin anunciar que termina.

SUBTÍTULOS: Si el contenido original tiene secciones claramente diferenciadas con títulos propios, convertí cada una en un <h2>. Máximo 2 subtítulos.

PROHIBIDO:
- Copiar líneas textuales del original sin reescribir
- <ul>, <ol> ni listas de ningún tipo
- "En conclusión", "En resumen", "En definitiva", "Para finalizar"
- "En primer lugar", "A continuación", "Por otro lado", "Cabe destacar"
- Más de 2 usos de <strong>

{_CATEGORY_GUIDE}
Categorías disponibles: {cat_list}

IMPORTANTE: Responde ÚNICAMENTE con JSON válido. Sin markdown, sin texto extra.
Comillas dobles estándar. Comillas SIMPLES dentro del HTML para atributos.
{{
  "title": "Título VIRAL entre 80 y 110 caracteres, estilo Infobae/Clarín. NUNCA menos de 75 caracteres. Con nombre, cifra o detalle concreto.",
  "content": "Noticia reescrita en HTML. {para_range} párrafos <p>. Mínimo {min_words} palabras. <h2> para secciones del original si las hay.",
  "category": "Exactamente una de estas opciones, sin modificar el nombre: {cat_list}",
  "summary": "EXACTAMENTE 20 palabras — ni una más ni una menos. Contá las palabras. Genera curiosidad e incluye la palabra clave.",
  "keyphrase": "frase clave de 2 a 4 palabras",
  "tags": ["etiqueta1", "etiqueta2", "etiqueta3", "etiqueta4", "etiqueta5"]
}}"""

    if provider == "anthropic":
        raw = _call_anthropic_text(api_key, model, prompt, max_tokens)
    else:
        client = _get_client(api_key, provider, api_base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
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
        if "summary" in result:
            result["summary"] = _normalize_summary(result["summary"], result.get("title", clean_subject))
        return result

    log.warning("No se pudo parsear JSON de IA. Raw (200): %s", raw[:200])
    fallback_title = first_body_line[:65] if first_body_line else clean_subject
    return {
        "title": fallback_title,
        "content": f"<p>{body[:1000]}</p>",
        "category": "General",
        "summary": body[:200],
    }
