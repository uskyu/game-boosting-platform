"""Focused tests for public and administrator site settings."""

from io import BytesIO

import pytest
from httpx import AsyncClient
from PIL import Image

from tests.conftest import auth_header

_png = BytesIO()
Image.new("RGB", (2, 2), "red").save(_png, format="PNG")
_PNG = _png.getvalue()


async def test_public_settings_are_lazy_created(client: AsyncClient):
    response = await client.get("/site/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["site_name"] == "游戏服务平台"
    assert data["site_logo_url"] is None
    assert data["logo_recommendation"]
    # 全站发单权限总开关默认要求已开通发单权限，且公开可读
    assert data["publish_permission_required"] is True


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


async def test_site_logo_normalizes_mismatched_and_rejects_oversized_files(
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
    assert response.status_code == 200
    assert response.json()["site_logo_url"].endswith(".jpg")
    response = await client.put(
        "/admin/site/logo",
        files={"logo": ("logo.png", BytesIO(b"x" * (10 * 1024 * 1024 + 1)), "image/png")},
        headers=headers,
    )
    assert response.status_code == 400


FOOTER_HTML = (
    '<a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener">'
    "粤ICP备2026000001号</a>"
)


async def test_footer_html_can_be_set_cleared_and_is_public(
    client: AsyncClient, admin_user: dict
):
    """页脚备案 HTML：保存后公开可读；不传保持不变；空串清空。"""
    headers = auth_header(admin_user)

    response = await client.put(
        "/admin/site/settings",
        json={"site_name": "平台", "footer_html": FOOTER_HTML},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["footer_html"] == FOOTER_HTML
    # 前台读公开接口就能拿到（页脚据此渲染）
    assert (await client.get("/site/settings")).json()["footer_html"] == FOOTER_HTML

    # 未传该字段：保持原值，不能被别的设置保存操作清空
    response = await client.put(
        "/admin/site/settings",
        json={"site_name": "平台改名"},
        headers=headers,
    )
    assert response.json()["footer_html"] == FOOTER_HTML

    # 空串表示清空（页脚不显示）
    response = await client.put(
        "/admin/site/settings",
        json={"site_name": "平台改名", "footer_html": ""},
        headers=headers,
    )
    assert response.json()["footer_html"] is None
    assert (await client.get("/site/settings")).json()["footer_html"] is None


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script>",
        '<div onclick="steal()">x</div>',
        '<a href="javascript:alert(1)">x</a>',
        '<iframe src="https://evil.example"></iframe>',
    ],
)
async def test_footer_html_rejects_script_content(
    client: AsyncClient, admin_user: dict, html: str
):
    """页脚 HTML 会渲染给所有访客，脚本类内容必须在保存处拒绝。"""
    response = await client.put(
        "/admin/site/settings",
        json={"site_name": "平台", "footer_html": html},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 422
