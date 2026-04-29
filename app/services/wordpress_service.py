from __future__ import annotations

import base64

import httpx


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
) -> int | None:
    """Sube una imagen a la biblioteca de medios de WordPress y devuelve su ID."""
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
            return resp.json().get("id")
    except Exception:
        pass
    return None


def find_category_by_name(
    site_url: str, api_user: str, app_password: str, category_name: str
) -> int | None:
    """Busca una categoría en WP por nombre (insensible a mayúsculas y acentos)."""
    import unicodedata

    def normalize(s: str) -> str:
        return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()

    categories = get_categories(site_url, api_user, app_password)
    norm_target = normalize(category_name)
    for cat in categories:
        if normalize(cat.get("name", "")) == norm_target:
            return cat["id"]
    return None


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
    payload: dict = {"title": title, "content": content, "status": status}
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

    with httpx.Client(timeout=30, verify=False) as client:
        resp = client.post(url, json=payload, headers=_headers(api_user, app_password))
    resp.raise_for_status()
    return resp.json()


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
