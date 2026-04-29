from __future__ import annotations

import json
import re

from groq import Groq


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
    """Elimina prefijos Fwd:, Re:, FW: del asunto."""
    while True:
        cleaned = _FWD_RE.sub("", subject).strip()
        if cleaned == subject:
            return cleaned
        subject = cleaned


def _clean_content(content: str) -> str:
    """Elimina fragmentos de JSON que Groq a veces añade al final del contenido."""
    # Cortar en el primer «,  o en líneas que parecen JSON residual
    cutoffs = ['\n«,', '\n",', '\n"category"', '\n«category»', '\n"summary"', '\n«summary»']
    for cut in cutoffs:
        idx = content.find(cut)
        if idx != -1:
            content = content[:idx]
    return content.strip()


def process_email_with_groq(
    api_key: str,
    model: str,
    base_prompt: str,
    subject: str,
    body: str,
) -> dict:
    client = Groq(api_key=api_key)
    clean_subject = _clean_subject(subject)

    prompt = f"""{base_prompt}

Asunto del correo: {clean_subject}

Contenido del correo:
{body[:4000]}

IMPORTANTE: Responde ÚNICAMENTE con un objeto JSON válido con comillas dobles estándar (sin texto adicional, sin markdown, sin explicaciones).
El JSON debe tener exactamente esta estructura:
{{
  "title": "Título atractivo del artículo (sin prefijos como Fwd o Re)",
  "content": "Contenido completo en HTML (usa <p>, <h2>, <strong>, <ul>, <li>)",
  "category": "Una de: Política, Economía, Tecnología, Deportes, Cultura, Sociedad, Internacional, General",
  "summary": "Resumen de 2-3 oraciones"
}}"""

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3000,
        temperature=0.6,
    )

    text = resp.choices[0].message.content.strip()

    # Normalizar comillas especiales que Groq a veces usa
    text = text.replace("«", '"').replace("»", '"').replace("“", '"').replace("”", '"')

    # Extraer el bloque JSON
    json_match = re.search(r"\{[\s\S]*\}", text)
    if json_match:
        try:
            result = json.loads(json_match.group())
            # Limpiar título y contenido
            if "title" in result:
                result["title"] = _clean_subject(result["title"])
            if "content" in result:
                result["content"] = _clean_content(result["content"])
            return result
        except json.JSONDecodeError:
            pass

    # Fallback: devolver el texto como contenido
    return {
        "title": clean_subject,
        "content": f"<p>{text}</p>",
        "category": "General",
        "summary": text[:200],
    }
