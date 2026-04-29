from __future__ import annotations

import json
import logging
import re

from groq import Groq

log = logging.getLogger("groq_service")


def test_groq_connection(api_key: str, model: str) -> tuple[bool, str]:
    try:
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Responde únicamente con la palabra: ok"}],
            max_tokens=10,
        )
        return True, resp.choices[0].message.content.strip()
    except Exception as exc:
        return False, str(exc)


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
        .replace(""", '"').replace(""", '"')
    )


def _extract_first_json(text: str) -> dict | None:
    """Extrae el primer objeto JSON válido del texto usando raw_decode."""
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
    """Recorta o extiende la summary para que tenga entre 18 y 22 palabras."""
    words = summary.strip().split()
    if len(words) >= 18:
        return " ".join(words[:22])
    # Demasiado corta: completar con fragmento del título
    extra = [w for w in title.split() if w.lower() not in summary.lower()]
    words = words + extra
    return " ".join(words[:20])


def _clean_content(content: str) -> str:
    """Elimina residuos de JSON que Groq a veces añade al contenido."""
    # Truncar en marcadores de campos JSON residuales
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

    # Si contiene «» el contenido es JSON crudo no normalizado — truncar antes del «
    if '«' in content or '»' in content:
        idx = content.find('«')
        content = content[:idx].strip() if idx > 0 else ''

    # Si empieza con { es un JSON embebido — intentar extraer el content real recursivamente
    if content.startswith('{'):
        inner = _extract_first_json(_normalize_quotes(content))
        if inner and 'content' in inner:
            return _clean_content(inner['content'])
        return ''

    return content


_DEFAULT_CATEGORIES = "Policiales, Política, Economía, Tecnología, Deportes, Cultura, Sociedad, Internacional, General"


def process_rss_with_groq(
    api_key: str,
    model: str,
    base_prompt: str,
    title: str,
    article_text: str,
    available_categories: list[str] | None = None,
) -> dict:
    """Procesa un artículo de RSS: reescribe sin plagio, sin mencionar fuente, para publicar en WP."""
    client = Groq(api_key=api_key)
    cat_list = ", ".join(available_categories) if available_categories else _DEFAULT_CATEGORIES

    prompt = f"""{base_prompt}

Título original de referencia: {title}

Contenido del artículo:
{article_text[:6000]}

INSTRUCCIONES DE REDACCIÓN:

Sos un periodista argentino con 20 años de experiencia en diarios nacionales. Escribís con voz activa, frases directas y datos concretos. El texto debe pasar cualquier detector de IA como escrito por humano.

LEGIBILIDAD: oraciones de entre 10 y 20 palabras. Vocabulario cotidiano, sin tecnicismos. Alternás oraciones cortas con algunas más largas para ritmo natural. Apuntás a 95% de legibilidad Flesch.

PÁRRAFOS: cada <p> con una idea concreta, bien separado del siguiente. Entre 4 y 6 párrafos.
- Primer párrafo: quién, qué, cuándo, dónde en 2 oraciones directas y fuertes.
- Párrafos del medio: contexto, antecedentes, declaraciones. Una cita textual clave va entre comillas con <strong> solo en la frase citada.
- Último párrafo: consecuencia, dato de cierre o proyección. Sin anunciar que termina.

SUBTÍTULOS: Usá 1 o 2 <h2> SOLO si el artículo trata claramente 2 o más temas diferenciados y supera los 5 párrafos. Si es un solo hecho o nota corta, NO uses ningún subtítulo.

PROHIBIDO:
- <ul>, <ol> ni listas de ningún tipo
- "En conclusión", "En resumen", "En definitiva", "Para finalizar"
- "En primer lugar", "A continuación", "Por otro lado", "Cabe destacar"
- Mencionar fuente, medio original, URLs ni sitios externos
- "Fuente:", "Según informó...", "El portal X indicó que..."
- Más de 2 usos de <strong>

IMPORTANTE: Responde ÚNICAMENTE con JSON válido. Sin markdown, sin texto extra.
Comillas dobles estándar. Comillas SIMPLES dentro del HTML para atributos.
{{
  "title": "Título SEO clickeable, máximo 65 caracteres, con dato concreto o pregunta, NO copiar el original",
  "content": "HTML periodístico con <p> bien separados y ocasionalmente <h2> solo si hay múltiples temas. Mínimo 400 palabras. Sin listas, sin secciones forzadas.",
  "category": "Una de: {cat_list}",
  "summary": "EXACTAMENTE 20 palabras — ni una más ni una menos. Contá las palabras antes de responder. Genera curiosidad e incluye la palabra clave.",
  "keyphrase": "frase clave de 2 a 4 palabras",
  "tags": ["etiqueta1", "etiqueta2", "etiqueta3", "etiqueta4", "etiqueta5"]
}}"""

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=6000,
        temperature=0.85,
    )

    raw = resp.choices[0].message.content.strip()
    log.debug("Groq RSS raw response (500 chars): %s", raw[:500])

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

    log.warning("No se pudo parsear JSON RSS de Groq. Raw (200): %s", raw[:200])
    return {
        "title": title,
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
) -> dict:
    client = Groq(api_key=api_key)
    clean_subject = _clean_subject(subject)
    cat_list = ", ".join(available_categories) if available_categories else _DEFAULT_CATEGORIES

    prompt = f"""{base_prompt}

Asunto del correo: {clean_subject}

Contenido del correo:
{body[:6000]}

INSTRUCCIONES DE REDACCIÓN:

Sos un periodista argentino con 20 años de experiencia en diarios nacionales. Escribís con voz activa, frases directas y datos concretos. El texto debe pasar cualquier detector de IA como escrito por humano.

LEGIBILIDAD: oraciones de entre 10 y 20 palabras. Vocabulario cotidiano, sin tecnicismos. Alternás oraciones cortas con algunas más largas para ritmo natural. Apuntás a 95% de legibilidad Flesch.

PÁRRAFOS: cada <p> con una idea concreta, bien separado del siguiente. Entre 4 y 6 párrafos.
- Primer párrafo: quién, qué, cuándo, dónde en 2 oraciones directas y fuertes.
- Párrafos del medio: contexto, antecedentes, declaraciones. Una cita textual clave va entre comillas con <strong> solo en la frase citada.
- Último párrafo: consecuencia, dato de cierre o proyección. Sin anunciar que termina.

SUBTÍTULOS: Usá 1 o 2 <h2> SOLO si el artículo trata claramente 2 o más temas diferenciados y supera los 5 párrafos. Si es un solo hecho o nota corta, NO uses ningún subtítulo.

PROHIBIDO:
- <ul>, <ol> ni listas de ningún tipo
- "En conclusión", "En resumen", "En definitiva", "Para finalizar"
- "En primer lugar", "A continuación", "Por otro lado", "Cabe destacar"
- Más de 2 usos de <strong>

IMPORTANTE: Responde ÚNICAMENTE con JSON válido. Sin markdown, sin texto extra.
Comillas dobles estándar. Comillas SIMPLES dentro del HTML para atributos.
{{
  "title": "Título SEO clickeable, máximo 65 caracteres, con dato concreto o pregunta, NO copiar el original",
  "content": "HTML periodístico con <p> bien separados y ocasionalmente <h2> solo si hay múltiples temas. Mínimo 400 palabras. Sin listas, sin secciones forzadas.",
  "category": "Una de: {cat_list}",
  "summary": "EXACTAMENTE 20 palabras — ni una más ni una menos. Contá las palabras antes de responder. Genera curiosidad e incluye la palabra clave.",
  "keyphrase": "frase clave de 2 a 4 palabras",
  "tags": ["etiqueta1", "etiqueta2", "etiqueta3", "etiqueta4", "etiqueta5"]
}}"""

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=6000,
        temperature=0.85,
    )

    raw = resp.choices[0].message.content.strip()
    log.debug("Groq raw response (500 chars): %s", raw[:500])

    # Normalizar comillas especiales y extraer el primer JSON válido
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

    # Fallback: no se pudo parsear JSON
    log.warning("No se pudo parsear JSON de Groq. Raw (200): %s", raw[:200])
    return {
        "title": clean_subject,
        "content": f"<p>{body[:1000]}</p>",
        "category": "General",
        "summary": body[:200],
    }
