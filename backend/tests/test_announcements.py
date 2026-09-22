"""Announcement CRUD, sanitization, permissions, and frequency behavior."""

from httpx import AsyncClient

from app.services.html_sanitizer import sanitize_announcement_html
from tests.conftest import auth_header


def test_announcement_sanitizer_preserves_rich_html_and_blocks_executable_content():
    safe = sanitize_announcement_html(
        """
        <style>
          :root { --announcement-accent: #2563eb; }
          .hero, .hero strong {
            display: flex;
            background: linear-gradient(135deg, #111, #333);
            color: white;
          }
          body { display: none; }
          @media (max-width: 640px) { .hero { padding: 8px; } }
        </style>
        <div id="hero" class="hero card" style="padding: 24px; border-radius: 16px;">
          <span style="color: #fff">漂亮公告</span>
          <img src="https://example.com/banner.png" onerror="alert(1)">
          <script>alert(1)</script>
          <a href="javascript:alert(1)" onclick="alert(1)">危险链接</a>
        </div>
        """
    )

    assert '<div id="hero" class="hero card"' in safe
    assert 'style="padding: 24px; border-radius: 16px"' in safe
    assert ".announcement-content .hero" in safe
    assert ".announcement-content {--announcement-accent: #2563eb}" in safe
    assert "@media (max-width: 640px)" in safe
    assert "body {" not in safe
    assert "<script" not in safe
    assert "onerror" not in safe
    assert "onclick" not in safe
    assert "javascript:" not in safe


def test_announcement_sanitizer_allows_safe_css_image_urls_only():
    safe = sanitize_announcement_html(
        '<div style="background-image: url(https://example.com/bg.png); color: white">安全</div>'
        '<div style="background-image: url(javascript:alert(1)); color: red">危险</div>'
    )

    assert "https://example.com/bg.png" in safe
    assert "javascript:" not in safe
    assert "background-image" not in safe.split("</div>")[1]


async def test_admin_can_create_safe_announcement_and_user_sees_it(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
):
    response = await client.post(
        "/admin/announcements",
        headers=auth_header(admin_user),
        json={
            "title": "维护公告",
            "content_html": '<p>今晚维护</p><script>alert("xss")</script><a href="javascript:alert(1)">危险链接</a>',
            "frequency": "DAILY",
            "is_enabled": True,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert "今晚维护" in data["safe_content_html"]
    assert "script" not in data["safe_content_html"]
    assert "javascript:" not in data["safe_content_html"]

    active = await client.get("/announcements/active", headers=auth_header(registered_user))
    assert active.status_code == 200
    assert active.json()["title"] == "维护公告"
    assert "script" not in active.json()["content_html"]

    shown = await client.post(
        f"/announcements/{data['id']}/shown",
        headers=auth_header(registered_user),
    )
    assert shown.status_code == 200

    active_again = await client.get("/announcements/active", headers=auth_header(registered_user))
    assert active_again.status_code == 200
    assert active_again.json() is None


async def test_announcement_admin_endpoints_require_admin(client: AsyncClient, registered_user: dict):
    response = await client.get("/admin/announcements", headers=auth_header(registered_user))
    assert response.status_code == 403


async def test_every_open_announcement_is_available_after_being_shown(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
):
    response = await client.post(
        "/admin/announcements",
        headers=auth_header(admin_user),
        json={
            "title": "每次提示",
            "content_html": "<strong>请留意</strong>",
            "frequency": "EVERY_OPEN",
            "is_enabled": True,
        },
    )
    assert response.status_code == 201
    announcement_id = response.json()["id"]

    first = await client.get("/announcements/active", headers=auth_header(registered_user))
    assert first.json()["id"] == announcement_id
    await client.post(f"/announcements/{announcement_id}/shown", headers=auth_header(registered_user))
    second = await client.get("/announcements/active", headers=auth_header(registered_user))
    assert second.json()["id"] == announcement_id
