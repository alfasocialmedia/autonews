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


def process_email_with_groq(
    api_key: str,
    model: str,
    base_prompt: str,
    subject: str,
    body: str,
) -> dict:
    client = Groq(api_key=api_key)

    prompt = f"""{base_prompt}

Asunto del correo: {subject}

Contenido del correo:
{body[:4000]}

IMPORTANTE: Responde ÚNICAMENTE con un objeto JSON válido (sin texto adicional, sin markdown, sin explicaciones).
El JSON debe tener exactamente esta estructura:
{{
  "title": "Título atractivo del artículo",
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

    # Intentar extraer JSON del texto
    json_match = re.search(r"\{[\s\S]*\}", text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Fallback: devolver el texto como contenido
    return {
        "title": subject,
        "content": f"<p>{text}</p>",
        "category": "General",
        "summary": text[:200],
    }
