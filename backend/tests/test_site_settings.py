"""Focused tests for public and administrator site settings."""

from io import BytesIO

from httpx import AsyncClient

from tests.conftest import auth_header

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


async def test_public_settings_are_lazy_created(client: AsyncClient):
    response = await client.get("/site/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["site_name"] == "游戏服务平台"
    assert data["site_logo_url"] is None
    assert data["logo_recommendation"]


async def test_admin_can_update_settings_and_upload_delete_logo(
    client: AsyncClient, admin_user: dict, monkeypatch, tmp_path
):
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    headers = auth_header(admin_user)
    response = await client.put(
        "/admin/site/settings",
        json={"site_name": "新平台", "site_description": "平台简介"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["site_name"] == "新平台"

    response = await client.put(
        "/admin/site/logo",
        files={"logo": ("logo.png", BytesIO(_PNG), "image/png")},
        headers=headers,
    )
    assert response.status_code == 200
    logo_url = response.json()["site_logo_url"]
    logo_path = tmp_path / "site" / logo_url.rsplit("/", 1)[-1]
    assert logo_path.is_file()

    response = await client.delete("/admin/site/logo", headers=headers)
    assert response.status_code == 200
    assert not logo_path.exists()
    assert (await client.get("/site/settings")).json()["site_logo_url"] is None


async def test_site_logo_rejects_unsupported_and_oversized_files(
    client: AsyncClient, admin_user: dict, monkeypatch, tmp_path
):
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    headers = auth_header(admin_user)
    response = await client.put(
        "/admin/site/logo",
        files={"logo": ("logo.exe", BytesIO(_PNG), "application/octet-stream")},
        headers=headers,
    )
    assert response.status_code == 400
    response = await client.put(
        "/admin/site/logo",
        files={"logo": ("logo.png", BytesIO(b"x" * (2 * 1024 * 1024 + 1)), "image/png")},
        headers=headers,
    )
    assert response.status_code == 400
