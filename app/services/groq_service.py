from __future__ import annotations

import json
import logging
import re

from groq import Groq

log = logging.getLogger(“groq_service”)


def test_groq_connection(api_key: str, model: str) -> tuple[bool, str]:
    try:
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{“role”: “user”, “content”: “Responde únicamente con la palabra: ok”}],
            max_tokens=10,
        )
        return True, resp.choices[0].message.content.strip()
    except Exception as exc:
        return False, str(exc)


_FWD_RE = re.compile(r”^(fwd?|re|fw)\s*:\s*”, re.IGNORECASE)


def _clean_subject(subject: str) -> str:
    while True:
        cleaned = _FWD_RE.sub(“”, subject).strip()
        if cleaned == subject:
            return cleaned
        subject = cleaned


def _normalize_quotes(text: str) -> str:
    return (
        text
        .replace(“«”, '”').replace(“»”, '”')
        .replace(““”, '”').replace(“””, '”')
    )


def _extract_first_json(text: str) -> dict | None:
    “””Extrae el primer objeto JSON válido del texto usando raw_decode.”””
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


def _clean_content(content: str) -> str:
    “””Elimina residuos de JSON que Groq a veces añade al contenido.”””
    # Truncar en marcadores de campos JSON residuales
    markers = [
        '\n«,', '\n”,',
        '\n”category”', '\n«category»',
        '\n”summary”',  '\n«summary»',
        '\n”keyphrase”', '\n«keyphrase»',
        '\n”title”', '\n«title»',
        '\n”tags”', '\n«tags»',
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


def process_email_with_groq(
    api_key: str,
    model: str,
    base_prompt: str,
    subject: str,
    body: str,
) -> dict:
    client = Groq(api_key=api_key)
    clean_subject = _clean_subject(subject)

    prompt = f”””{base_prompt}

Asunto del correo: {clean_subject}

Contenido del correo:
{body[:6000]}

IMPORTANTE: Responde ÚNICAMENTE con un objeto JSON válido. Usa comillas dobles estándar (sin «», sin “”, sin markdown, sin texto adicional).
Usa comillas SIMPLES dentro del HTML para atributos (href='url' no href=”url”).
El JSON debe tener exactamente esta estructura:
{{
  “title”: “Título SEO original y clickeable: incluye la palabra clave principal, usa números o datos concretos o una pregunta o genera urgencia/curiosidad, máximo 65 caracteres, NO copiar el titular original”,
  “content”: “Artículo periodístico COMPLETO en HTML, mínimo 500 palabras. Usa <p> para párrafos, <h2> para subtítulos, <strong> para destacados, <ul><li> para listas. Desarrolla todos los puntos con contexto, antecedentes y detalles.”,
  “category”: “Una de: Política, Economía, Tecnología, Deportes, Cultura, Sociedad, Internacional, General”,
  “summary”: “Meta descripción SEO de MÁXIMO 20 palabras: describe el contenido, NO repetir el título, incluir la palabra clave de forma natural, generar curiosidad para el clic”,
  “keyphrase”: “frase clave de 2 a 4 palabras que resume el tema principal del artículo”,
  “tags”: [“etiqueta1”, “etiqueta2”, “etiqueta3”, “etiqueta4”, “etiqueta5”]
}}”””

    resp = client.chat.completions.create(
        model=model,
        messages=[{“role”: “user”, “content”: prompt}],
        max_tokens=6000,
        temperature=0.7,
    )

    raw = resp.choices[0].message.content.strip()
    log.debug(“Groq raw response (500 chars): %s”, raw[:500])

    # Normalizar comillas especiales y extraer el primer JSON válido
    text = _normalize_quotes(raw)
    result = _extract_first_json(text)

    if result:
        if “title” in result:
            result[“title”] = _clean_subject(result[“title”])
        if “content” in result:
            result[“content”] = _clean_content(result[“content”])
            if not result[“content”]:
                log.warning(“Content vacío tras limpieza, usando cuerpo original”)
                result[“content”] = f”<p>{body[:1000]}</p>”
        return result

    # Fallback: no se pudo parsear JSON
    log.warning(“No se pudo parsear JSON de Groq. Raw (200): %s”, raw[:200])
    return {
        “title”: clean_subject,
        “content”: f”<p>{body[:1000]}</p>”,
        “category”: “General”,
        “summary”: body[:200],
    }
