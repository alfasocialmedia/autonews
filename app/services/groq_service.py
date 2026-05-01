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
        client = _get_client(api_key, provider, api_base_url)
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
    client = _get_client(api_key, provider, api_base_url)
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

Título original de referencia: {title}

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

TÍTULO LLAMATIVO — OBLIGATORIO:
El título debe generar el impulso inmediato de hacer clic. Entre 70 y 90 caracteres. Usá UNA de estas fórmulas:
1. Verbo de impacto + dato concreto: "Confirmaron 40 despidos en la empresa líder del sector y hay más en camino"
2. Cifra + consecuencia directa: "Sube 20% la tarifa de luz: así impacta en el bolsillo de los vecinos"
3. Pregunta que genera curiosidad o tensión: "¿Por qué el Gobierno frenó el proyecto más esperado del año y qué viene ahora?"
4. Revelación o secreto: "El dato que nadie contó sobre el cierre de la planta que dejó sin trabajo a 300 personas"
5. Conflicto o giro inesperado: "Iban a inaugurar la obra más importante de la provincia y encontraron esto debajo del suelo"
NUNCA un título plano o descriptivo. NUNCA menos de 60 caracteres. Sin puntos al final. Sin comillas en el título.

IMPORTANTE: Responde ÚNICAMENTE con JSON válido. Sin markdown, sin texto extra.
Comillas dobles estándar. Comillas SIMPLES dentro del HTML para atributos.
{{
  "title": "Título IMPACTANTE entre 70 y 90 caracteres usando una de las fórmulas indicadas. Generá el impulso de hacer clic. NUNCA menos de 60 caracteres.",
  "content": "HTML periodístico. {para_range} párrafos {content_hint}. Cada <p> máximo 2 oraciones. Mínimo {min_words} palabras. Sin listas.",
  "category": "Exactamente una de estas opciones, sin modificar el nombre: {cat_list}",
  "summary": "EXACTAMENTE 20 palabras — ni una más ni una menos. Contá las palabras antes de responder. Genera curiosidad e incluye la palabra clave.",
  "keyphrase": "frase clave de 2 a 4 palabras",
  "tags": ["etiqueta1", "etiqueta2", "etiqueta3", "etiqueta4", "etiqueta5"]
}}"""

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
    provider: str = "groq",
    api_base_url: str | None = None,
) -> dict:
    client = _get_client(api_key, provider, api_base_url)
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

CONTENIDO A TRANSFORMAR EN NOTICIA:
{body[:8000]}

INSTRUCCIONES DE REDACCIÓN:

Sos un periodista argentino con 20 años de experiencia en diarios nacionales. Tu tarea es REESCRIBIR completamente el contenido anterior como una noticia periodística. NO copies frases textuales — reformulá cada idea con voz propia, activa y directa. El texto debe sonar 100% humano.

TÍTULO LLAMATIVO — OBLIGATORIO:
El título debe generar el impulso inmediato de hacer clic. Entre 70 y 90 caracteres. Transformá el hecho central usando UNA de estas fórmulas:
1. Verbo de impacto + dato concreto: "Confirmaron 40 despidos en la empresa líder del sector y hay más en camino"
2. Cifra + consecuencia directa: "Sube 20% la tarifa de luz: así impacta en el bolsillo de los vecinos"
3. Pregunta que genera curiosidad o tensión: "¿Por qué el Gobierno frenó el proyecto más esperado del año y qué viene ahora?"
4. Revelación o secreto: "El dato que nadie contó sobre el cierre de la planta que dejó sin trabajo a 300 personas"
5. Conflicto o giro inesperado: "Iban a inaugurar la obra más importante de la provincia y encontraron esto debajo del suelo"
Si el contenido empieza con "{first_body_line[:60]}", esa es la noticia — reescribila con gancho. NUNCA copies el asunto del correo. NUNCA título plano o descriptivo. NUNCA menos de 60 caracteres. Sin puntos al final.

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
  "title": "Título IMPACTANTE entre 70 y 90 caracteres usando una de las fórmulas indicadas. Generá el impulso de hacer clic. NUNCA menos de 60 caracteres.",
  "content": "Noticia reescrita en HTML. {para_range} párrafos <p>. Mínimo {min_words} palabras. <h2> para secciones del original si las hay.",
  "category": "Exactamente una de estas opciones, sin modificar el nombre: {cat_list}",
  "summary": "EXACTAMENTE 20 palabras — ni una más ni una menos. Contá las palabras. Genera curiosidad e incluye la palabra clave.",
  "keyphrase": "frase clave de 2 a 4 palabras",
  "tags": ["etiqueta1", "etiqueta2", "etiqueta3", "etiqueta4", "etiqueta5"]
}}"""

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
