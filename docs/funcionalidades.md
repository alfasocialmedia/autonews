# AutoNews — Documentación de funcionalidades

Referencia completa de todas las capacidades del sistema. Se actualiza con cada cambio relevante.

---

## Índice

1. [Flujos de ingesta](#1-flujos-de-ingesta)
2. [Scraping de artículos](#2-scraping-de-artículos)
3. [Imágenes y multimedia](#3-imágenes-y-multimedia)
4. [Procesamiento con IA (Groq)](#4-procesamiento-con-ia-groq)
5. [Audio TTS](#5-audio-tts)
6. [Publicación en WordPress](#6-publicación-en-wordpress)
7. [Difusión por WhatsApp](#7-difusión-por-whatsapp)
8. [Filtros y control de flujo](#8-filtros-y-control-de-flujo)
9. [Panel de administración](#9-panel-de-administración)
10. [Seguridad y cifrado](#10-seguridad-y-cifrado)

---

## 1. Flujos de ingesta

### 1.1 Correo IMAP

El worker revisa cuentas de correo configuradas cada **60 segundos**.

- Lee los correos no leídos via IMAP.
- Extrae el cuerpo del mensaje (texto plano o HTML).
- Detecta imágenes adjuntas (adjuntos `image/*`) o URLs de imagen en el cuerpo.
- Pasa el contenido al procesador de IA.
- Publica el resultado en todos los sitios WordPress activos.
- Marca el correo como leído al finalizar.

### 1.2 Feeds RSS

El worker revisa feeds RSS activos cada **5 minutos** (configurable por feed).

Cada feed tiene su propia configuración:
- **Intervalo de chequeo** en minutos.
- **Artículos por ciclo** (cuántos procesar por revisión).
- **Máximo diario** (límite de publicaciones por día).
- **Filtro de palabras clave** (publicar solo si el artículo las contiene).
- **Categoría forzada** (sobreescribe la categoría detectada por la IA).

**Lógica de cutoff**: solo se procesan artículos publicados después del último chequeo. Para feeds nuevos, el rango es las últimas 48 horas.

---

## 2. Scraping de artículos

Cuando el RSS trae solo un excerpt corto (o siempre, para obtener la `og:image`), se scrapea la URL original del artículo.

Archivo: `app/services/rss_service.py` — función `scrape_full_article()`

**Retorna**: `(texto, og_image, inline_images, embeds)`

### 2.1 Estrategia de extracción de texto

**Prioridad 1 — JSON-LD (schema.org)**

Busca `<script type="application/ld+json">` con tipos `NewsArticle`, `Article`, `ReportageNewsArticle` o `BlogPosting`. Extrae el campo `articleBody`. Funciona incluso cuando el HTML está parcialmente oculto por paywalls o renderizado con JavaScript.

**Prioridad 2 — Scraping HTML clásico**

1. Localiza el contenedor del artículo con selectores progresivos:
   - `itemprop="articleBody"`
   - Clases comunes de WordPress/CMS: `entry-content`, `post-content`, `article-body`, `td-post-content`, `jeg_content`, `nota-cuerpo`, etc.
   - Tag `<article>`
   - Fallback a `<main>` o divs con clase/id que contengan `content`, `nota`, `story`, etc.
2. Elimina ruido: scripts, estilos, nav, header, footer, aside, iframes, publicidad, banners, widgets, popups, cookies, elementos sociales/share.
3. Extrae texto limpio con `get_text()`.

**Límite**: 12.000 caracteres de texto por artículo.

### 2.2 Headers HTTP

Se usan headers realistas de Chrome 124 con `Accept-Language: es-AR` para evitar bloqueos. Timeout de 15 segundos, sigue redirecciones.

---

## 3. Imágenes y multimedia

### 3.1 Imagen destacada (og:image)

Al scrapear se extrae en este orden de prioridad:
1. `<meta property="og:image">` o `<meta name="twitter:image">`
2. Primera imagen grande dentro del `<article>` o `<main>` (excluye logos, iconos, avatares, pixels de tracking)

Si el feed RSS ya trae una imagen (`media:content`, `enclosure`), se prefiere igualmente la `og:image` del artículo por tener mayor resolución.

**Tamaño mínimo aceptado desde el feed**: 400 px de ancho.

### 3.2 Imagen generada por IA (fallback)

Si no se encuentra ninguna imagen, se genera una automáticamente con **Pollinations.ai** (gratuito, sin API key):

- Prompt: `"professional news photo editorial style, {título}, {hint de categoría}"`
- Resolución: 1200×630 px (proporción Open Graph)
- Modelo: `flux`

### 3.3 Imágenes inline del artículo

Al scrapear, **antes** de eliminar el ruido HTML, se extraen hasta **3 imágenes editoriales** del cuerpo del artículo:

- Se excluye la `og:image` (ya va como imagen destacada).
- Se filtran imágenes pequeñas (< 250 px de ancho o < 150 px de alto si las dimensiones están declaradas).
- Se filtran URLs que contengan: `logo`, `icon`, `avatar`, `pixel`, `tracking`, `spinner`, `btn`, `arrow`, `spacer`, `badge`, `placeholder`.

Estas imágenes se **inyectan como bloques `wp:image` de Gutenberg** distribuidos uniformemente entre los párrafos del contenido generado por la IA.

### 3.4 Embeds de redes sociales

Al scrapear, **antes** de eliminar el ruido HTML, se detectan los siguientes embeds en el cuerpo del artículo:

| Fuente | Detección en HTML estático | Bloque Gutenberg generado |
|--------|---------------------------|--------------------------|
| **YouTube** | `<iframe src="youtube.com/embed/VIDEO_ID">` | `wp:embed providerNameSlug: youtube` |
| **Twitter / X** | `<blockquote class="twitter-tweet">` con URL de status | `wp:embed providerNameSlug: twitter` |
| **Instagram** | `<blockquote class="instagram-media">` con URL de post | `wp:embed providerNameSlug: instagram` |
| **Facebook** | `<iframe src="facebook.com/plugins/...">` | `wp:embed providerNameSlug: facebook` |

Los embeds se convierten a bloques `<!-- wp:embed -->` de Gutenberg y se agregan al **final del contenido** del post.

> **Limitación**: el scraping es HTML estático (sin navegador/JS). Algunos embeds de Instagram y Facebook que se inyectan dinámicamente con JavaScript no estarán en el HTML inicial y no serán detectados.

### 3.5 Descarga y upload a WordPress

La imagen destacada se descarga y sube al media library de WordPress:
- `POST /wp-json/wp/v2/media` con el binario de la imagen.
- Se guarda el `media_id` para asignarlo como `featured_media` del post.
- El nombre de archivo se deriva de la URL original (caracteres especiales reemplazados).

### 3.6 Imágenes desde Google Drive

Soporte para carpetas de Google Drive como fuente de imágenes (configuración por feed):
- Formato especial de URL: `gdrive-folder:FOLDER_ID`
- Requiere `GOOGLE_DRIVE_API_KEY` en las variables de entorno.
- Lista imágenes en la carpeta raíz; si no hay, busca en subcarpetas (un nivel).
- Usa la primera imagen encontrada (ordenada por nombre).

---

## 4. Procesamiento con IA (Groq)

Proveedor configurable: **Groq** (por defecto), **OpenRouter** u otro compatible con la API de OpenAI.

El modelo por defecto es `llama-3.3-70b-versatile`.

La IA produce:
- **Título**: entre 80 y 110 caracteres, optimizado para SEO.
- **Contenido**: artículo reescrito en HTML (`<p>` tags), con voz periodística propia.
- **Categoría**: seleccionada entre las categorías disponibles en WordPress.
- **Resumen/excerpt**: máximo 20 palabras, para la meta description.
- **Tags**: lista de etiquetas relevantes (se crean automáticamente en WP si no existen).
- **Keyphrase SEO**: frase clave para Yoast SEO.

El prompt base es configurable desde el panel de administración.

**Fallback de tokens**: si el proveedor devuelve error 402 (límite de tokens), se reintenta con un modelo más pequeño (`llama-3.1-8b-instant`).

---

## 5. Audio TTS

El sistema puede generar audio narrado del artículo y adjuntarlo al post de WordPress como un reproductor de audio.

**Prioridad 1 — ElevenLabs** (requiere API key y créditos):
- Configurable: voz, modelo.
- Genera MP3 de alta calidad.

**Prioridad 2 — Edge TTS** (gratuito, sin API key):
- Voces en español de Microsoft.
- Fallback automático si ElevenLabs falla o no está configurado.

El audio se sube al media library de WordPress y se inserta al **inicio del contenido** como bloque `<!-- wp:audio -->` con un `<audio controls>` nativo.

---

## 6. Publicación en WordPress

Autenticación: **Basic Auth** con usuario + Application Password (base64).  
Endpoint: `POST /wp-json/wp/v2/posts`

### 6.1 Campos enviados al crear el post

| Campo | Descripción |
|-------|-------------|
| `title` | Título generado por IA (80–110 caracteres) |
| `content` | HTML con párrafos, imágenes inline y embeds multimedia |
| `excerpt` | Resumen de 20 palabras |
| `status` | `draft` o `publish` (configurable por sitio) |
| `date` | Fecha/hora Argentina (UTC-3) |
| `categories` | IDs de categorías (resueltas o creadas automáticamente) |
| `tags` | IDs de etiquetas (creadas automáticamente si no existen) |
| `featured_media` | ID del media subido como imagen destacada |
| `meta._yoast_wpseo_focuskw` | Keyphrase para Yoast SEO |
| `meta._yoast_wpseo_metadesc` | Meta description para Yoast SEO |

### 6.2 Categorías

- **Forzada por feed**: si el feed tiene una categoría asignada, se usa siempre (ignora la de la IA).
- **Por nombre**: si el feed tiene nombre de categoría pero no ID, se busca en WP y se asigna.
- **Detectada por IA**: Groq elige la categoría entre las disponibles en el sitio WP.
- **Creación automática**: si la categoría no existe en WP, se crea.

### 6.3 Soporte multi-sitio

El sistema puede publicar en **múltiples sitios WordPress simultáneamente**. La imagen y el audio se descargan/generan una sola vez y se suben a cada sitio.

---

## 7. Difusión por WhatsApp

Tras publicar exitosamente en WordPress (primer sitio), se envía una notificación a grupos de WhatsApp configurados.

- Integración con **Evolution API**.
- Plantilla de mensaje configurable: `{title}`, `{summary}`, `{url}`.
- Difusión a múltiples grupos con JIDs configurados.
- Solo se ejecuta si la difusión está habilitada en la configuración.

---

## 8. Filtros y control de flujo

### 8.1 Filtro de palabras clave (RSS)

Cada feed puede tener un filtro de palabras clave separadas por comas. El artículo se publica solo si el título o el cuerpo contienen **alguna** de esas palabras (case-insensitive).

El filtro se aplica en dos momentos:
1. Sobre el título antes de scrapear (rápido).
2. Sobre el cuerpo completo después de scrapear (más preciso).

Los artículos descartados se marcan como `skipped` en la base de datos para no revisarlos nuevamente.

### 8.2 Deduplicación

Cada artículo RSS se identifica por su `guid`. Si ya existe en la base de datos, se omite sin reprocesar.

### 8.3 Límites de publicación

- **Máximo diario por feed**: configurable; al alcanzarlo, el feed se omite hasta el día siguiente.
- **Artículos por ciclo**: cuántos artículos procesar por cada revisión del feed.

---

## 9. Panel de administración

Aplicación web FastAPI + Jinja2 + Bootstrap 5.

### Secciones

| Sección | Funcionalidad |
|---------|---------------|
| Dashboard | Estadísticas: posts publicados, correos procesados, errores recientes |
| Correos | Bandeja de noticias recibidas por email |
| RSS | Lista de artículos detectados por feed, publicación manual |
| Feeds | Configuración de feeds RSS (URL, intervalo, límites, categoría, filtro) |
| Configuración → Email | Cuentas IMAP con prueba de conexión |
| Configuración → WordPress | Sitios WP con prueba de conexión y carga de categorías |
| Configuración → Groq IA | API key, modelo, prompt base |
| Configuración → ElevenLabs | API key, voz, modelo para TTS |
| Configuración → Edge TTS | Voz de Microsoft para TTS gratuito |
| Configuración → WhatsApp | Evolution API, grupos de difusión, plantilla de mensaje |
| Logs | Registro de eventos del worker en tiempo real |

### Publicación manual desde el panel

Desde la sección RSS se puede forzar la publicación inmediata de cualquier artículo detectado, independientemente de los límites diarios o el estado de procesamiento.

---

## 10. Seguridad y cifrado

- **Contraseñas de correo, API keys y app passwords**: cifradas en base de datos con **Fernet** (AES-128-CBC). La clave de cifrado (`ENCRYPTION_KEY`) vive solo en el `.env`.
- **Contraseñas de usuarios**: hash **bcrypt** via passlib.
- **Sesiones**: firmadas con `itsdangerous` usando `SECRET_KEY`.
- **Panel web**: el panel nunca expone claves completas en pantalla (solo primeros/últimos caracteres).
- **Acceso**: autenticación requerida para todas las rutas del panel.

---

## Historial de cambios relevantes

| Fecha | Cambio |
|-------|--------|
| 2026-05-04 | Soporte para imágenes inline y embeds de YouTube/Twitter/Instagram/Facebook extraídos del artículo scrapeado e inyectados como bloques Gutenberg |
| 2026-05 | Reintentar con tokens asequibles cuando OpenRouter devuelve error 402 |
| 2026-05 | Aumentar límite de texto fuente de 8.000 a 12.000 caracteres |
| 2026-05 | Soporte para newsletter JIDs en webhook de WhatsApp |
| 2026-05 | Imagen fallback generada con IA (Pollinations.ai) cuando no hay imagen en el artículo |
| 2026-05 | Soporte multi-sitio WordPress (publica en varios sitios simultáneamente) |
| 2026-05 | Audio TTS con ElevenLabs + Edge TTS como fallback gratuito |
| 2026-05 | Integración con Evolution API para difusión por WhatsApp |
| 2026-05 | Feeds RSS con filtros de palabras clave, límites diarios e intervalo configurable |
| 2026-05 | Scraping de artículos con prioridad JSON-LD sobre HTML clásico |
| 2026-05 | Soporte para imágenes desde Google Drive (carpetas públicas) |
