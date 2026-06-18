from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger("wordpress_service")

_TZ_AR = timezone(timedelta(hours=-3))  # Argentina UTC-3, sin horario de verano


def _now_ar() -> str:
    """Hora actual argentina en formato ISO 8601 para la API de WordPress."""
    return datetime.now(_TZ_AR).strftime("%Y-%m-%dT%H:%M:%S")


def _headers(api_user: str, app_password: str) -> dict:
    creds = base64.b64encode(f"{api_user}:{app_password}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


def test_wordpress_connection(site_url: str, api_user: str, app_password: str) -> tuple[bool, str]:
    url = site_url.rstrip("/") + "/wp-json/wp/v2/posts?per_page=1&status=draft"
    try:
        with httpx.Client(timeout=15, verify=False) as client:
            resp = client.get(url, headers=_headers(api_user, app_password))
        if resp.status_code == 200:
            return True, f"Conectado correctamente como: {api_user}"
        # Credenciales válidas pero sin permiso para listar usuarios (plugin de seguridad)
        if resp.status_code == 401:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if data.get("code") == "rest_user_cannot_view":
                return True, f"Conectado correctamente como: {api_user}"
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as exc:
        return False, str(exc)


def upload_media(
    site_url: str,
    api_user: str,
    app_password: str,
    image_bytes: bytes,
    filename: str,
    mime_type: str,
) -> tuple[int, str] | None:
    """Sube una imagen a la biblioteca de medios de WordPress. Devuelve (id, source_url) o None."""
    url = site_url.rstrip("/") + "/wp-json/wp/v2/media"
    creds = base64.b64encode(f"{api_user}:{app_password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": mime_type,
    }
    try:
        with httpx.Client(timeout=30, verify=False) as client:
            resp = client.post(url, content=image_bytes, headers=headers)
        if resp.status_code in (200, 201):
            data = resp.json()
            return data.get("id"), data.get("source_url", "")
    except Exception:
        pass
    return None


def upload_audio(
    site_url: str,
    api_user: str,
    app_password: str,
    audio_bytes: bytes,
    filename: str,
) -> tuple[int, str] | None:
    """Sube un MP3 a la biblioteca de WordPress. Devuelve (media_id, source_url) o None."""
    url = site_url.rstrip("/") + "/wp-json/wp/v2/media"
    creds = base64.b64encode(f"{api_user}:{app_password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "audio/mpeg",
    }
    try:
        with httpx.Client(timeout=60, verify=False) as client:
            resp = client.post(url, content=audio_bytes, headers=headers)
        if resp.status_code in (200, 201):
            data = resp.json()
            return data.get("id"), data.get("source_url", "")
    except Exception as exc:
        log.warning("upload_audio error: %s", exc)
    return None


def find_category_by_name(
    site_url: str, api_user: str, app_password: str, category_name: str
) -> int | None:
    """Busca una categoría en WP por nombre, primero con search API luego listando todas."""
    import unicodedata

    def normalize(s: str) -> str:
        return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()

    base = site_url.rstrip("/") + "/wp-json/wp/v2/categories"
    hdrs = _headers(api_user, app_password)
    norm_target = normalize(category_name)

    # 1. Búsqueda directa por nombre vía endpoint search (más rápido y confiable)
    try:
        with httpx.Client(timeout=15, verify=False) as client:
            resp = client.get(
                base,
                params={"search": category_name, "per_page": 20},
                headers=hdrs,
            )
        if resp.status_code == 200:
            for cat in resp.json():
                if normalize(cat.get("name", "")) == norm_target:
                    return cat["id"]
    except Exception as exc:
        log.warning("Error buscando categoría '%s' por search: %s", category_name, exc)

    # 2. Fallback: listar todas las categorías y comparar
    categories = get_categories(site_url, api_user, app_password)
    for cat in categories:
        if normalize(cat.get("name", "")) == norm_target:
            return cat["id"]

    return None


def get_or_create_category(
    site_url: str, api_user: str, app_password: str, category_name: str
) -> int | None:
    """Busca la categoría en WP; si no existe la crea y devuelve el ID."""
    cat_id = find_category_by_name(site_url, api_user, app_password, category_name)
    if cat_id:
        return cat_id
    base = site_url.rstrip("/") + "/wp-json/wp/v2/categories"
    try:
        with httpx.Client(timeout=15, verify=False) as client:
            resp = client.post(base, json={"name": category_name}, headers=_headers(api_user, app_password))
        if resp.status_code in (200, 201):
            return resp.json().get("id")
        if resp.status_code == 400:
            # WP devuelve term_exists cuando la categoría ya existe con ese slug
            error_data = resp.json()
            if error_data.get("code") == "term_exists":
                term_id = error_data.get("data", {}).get("term_id")
                if term_id:
                    log.info("Categoría '%s' ya existía (term_exists), usando ID %s", category_name, term_id)
                    return term_id
        log.warning("No se pudo crear categoría '%s': HTTP %s — %s", category_name, resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("No se pudo crear categoría '%s': %s", category_name, exc)
    return None


def test_plugin_connection(site_url: str, plugin_api_key: str) -> tuple[bool, str]:
    """Verifica la conexión con el plugin AutoNews Connector instalado en el sitio WP."""
    url = site_url.rstrip("/") + "/wp-json/autonews/v1/status"
    try:
        with httpx.Client(timeout=15, verify=False) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {plugin_api_key}"})
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return True, f"Plugin conectado — {data.get('site_name', site_url)} (WP {data.get('wp_version', '?')})"
            return False, f"Respuesta inesperada: {resp.text[:200]}"
        if resp.status_code == 401:
            return False, "Token inválido — copiá el token desde el plugin en tu WordPress."
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        return False, str(exc)


def create_post_via_plugin(
    site_url: str,
    plugin_api_key: str,
    title: str,
    content: str,
    status: str = "draft",
    categories: list[str] | None = None,
    tags: list[str] | None = None,
    excerpt: str = "",
    keyphrase: str = "",
    featured_image_url: str | None = None,
    featured_image_bytes: bytes | None = None,
    featured_image_filename: str | None = None,
    featured_image_mimetype: str | None = None,
) -> dict:
    """Publica via plugin AutoNews Connector. Devuelve {'id': ..., 'link': ...}.
    El plugin resuelve categorías y etiquetas por nombre, sube la imagen y aplica SEO.
    """
    import base64 as _b64
    import requests as _req

    endpoint = site_url.rstrip("/") + "/wp-json/autonews/v1/publish"
    headers = {
        "Authorization": f"Bearer {plugin_api_key}",
        "Content-Type": "application/json",
    }
    body: dict = {
        "title": title,
        "content": content,
        "excerpt": excerpt,
        "status": status,
        "categories": [c for c in (categories or []) if c],
        "tags": [t for t in (tags or []) if t],
        "keyphrase": keyphrase,
        "meta_description": excerpt,
    }
    if featured_image_url:
        body["featured_image_url"] = featured_image_url
    elif featured_image_bytes:
        body["featured_image_base64"] = _b64.b64encode(featured_image_bytes).decode()
        body["featured_image_filename"] = featured_image_filename or "portada.jpg"
        body["featured_image_mimetype"] = featured_image_mimetype or "image/jpeg"

    resp = _req.post(endpoint, json=body, headers=headers, timeout=90, verify=False)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ValueError(f"Plugin error: {data.get('error', 'unknown')}")
    return {"id": data["post_id"], "link": data["post_url"]}


def create_post(
    site_url: str,
    api_user: str,
    app_password: str,
    title: str,
    content: str,
    status: str = "draft",
    category_ids: list[int] | None = None,
    featured_media_id: int | None = None,
    excerpt: str = "",
    tag_ids: list[int] | None = None,
    keyphrase: str = "",
) -> dict:
    url = site_url.rstrip("/") + "/wp-json/wp/v2/posts"
    payload: dict = {"title": title, "content": content, "status": status, "date": _now_ar()}
    if category_ids:
        payload["categories"] = category_ids
    if featured_media_id:
        payload["featured_media"] = featured_media_id
    if excerpt:
        payload["excerpt"] = excerpt
    if tag_ids:
        payload["tags"] = tag_ids
    if keyphrase or excerpt:
        payload["meta"] = {}
        if keyphrase:
            payload["meta"]["_yoast_wpseo_focuskw"] = keyphrase
        if excerpt:
            payload["meta"]["_yoast_wpseo_metadesc"] = excerpt

    hdrs = _headers(api_user, app_password)
    with httpx.Client(timeout=30, verify=False) as client:
        resp = client.post(url, json=payload, headers=hdrs)
        resp.raise_for_status()
        post = resp.json()

        # Actualizar Yoast SEO vía PATCH por si el meta no quedó guardado en el POST inicial
        post_id = post.get("id")
        if post_id and (keyphrase or excerpt):
            yoast_meta: dict = {}
            if keyphrase:
                yoast_meta["_yoast_wpseo_focuskw"] = keyphrase
            if excerpt:
                yoast_meta["_yoast_wpseo_metadesc"] = excerpt
            try:
                patch_resp = client.patch(
                    f"{url}/{post_id}",
                    json={"meta": yoast_meta},
                    headers=hdrs,
                )
                if patch_resp.status_code not in (200, 201):
                    log.warning(
                        "Yoast PATCH HTTP %s: %s",
                        patch_resp.status_code,
                        patch_resp.text[:300],
                    )
                else:
                    saved_meta = patch_resp.json().get("meta", {})
                    log.info(
                        "Yoast meta guardado — focuskw=%r metadesc=%r",
                        saved_meta.get("_yoast_wpseo_focuskw"),
                        saved_meta.get("_yoast_wpseo_metadesc"),
                    )
            except Exception as exc:
                log.warning("Yoast PATCH error: %s", exc)

    return post


def get_or_create_tags(
    site_url: str, api_user: str, app_password: str, tag_names: list[str]
) -> list[int]:
    """Obtiene o crea etiquetas en WordPress y devuelve sus IDs."""
    base = site_url.rstrip("/") + "/wp-json/wp/v2/tags"
    headers = _headers(api_user, app_password)
    tag_ids = []
    with httpx.Client(timeout=15, verify=False) as client:
        for name in tag_names[:10]:
            name = name.strip()
            if not name:
                continue
            try:
                # Buscar si ya existe
                resp = client.get(base, params={"search": name}, headers=headers)
                if resp.status_code == 200:
                    existing = [t for t in resp.json() if t["name"].lower() == name.lower()]
                    if existing:
                        tag_ids.append(existing[0]["id"])
                        continue
                # Crear si no existe
                resp = client.post(base, json={"name": name}, headers=headers)
                if resp.status_code in (200, 201):
                    tag_ids.append(resp.json()["id"])
            except Exception:
                continue
    return tag_ids


def get_categories(site_url: str, api_user: str, app_password: str) -> list[dict]:
    url = site_url.rstrip("/") + "/wp-json/wp/v2/categories?per_page=100"
    try:
        with httpx.Client(timeout=15, verify=False) as client:
            resp = client.get(url, headers=_headers(api_user, app_password))
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []
