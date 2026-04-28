from __future__ import annotations

import base64

import httpx


def _headers(api_user: str, app_password: str) -> dict:
    creds = base64.b64encode(f"{api_user}:{app_password}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


def test_wordpress_connection(site_url: str, api_user: str, app_password: str) -> tuple[bool, str]:
    url = site_url.rstrip("/") + "/wp-json/wp/v2/users/me"
    try:
        with httpx.Client(timeout=15, verify=False) as client:
            resp = client.get(url, headers=_headers(api_user, app_password))
        if resp.status_code == 200:
            name = resp.json().get("name", api_user)
            return True, f"Conectado como: {name}"
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as exc:
        return False, str(exc)


def create_post(
    site_url: str,
    api_user: str,
    app_password: str,
    title: str,
    content: str,
    status: str = "draft",
    category_ids: list[int] | None = None,
) -> dict:
    url = site_url.rstrip("/") + "/wp-json/wp/v2/posts"
    payload: dict = {"title": title, "content": content, "status": status}
    if category_ids:
        payload["categories"] = category_ids

    with httpx.Client(timeout=30, verify=False) as client:
        resp = client.post(url, json=payload, headers=_headers(api_user, app_password))
    resp.raise_for_status()
    return resp.json()


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
