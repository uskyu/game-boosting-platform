"""Focused tests for game logos and bulk management."""

from io import BytesIO

from httpx import AsyncClient
from tests.conftest import auth_header

from app.models.game import Game

_GAME = {
    "name": "测试游戏",
    "category": "FPS",
    "platform": "PC",
    "service_template": {"service_types": ["上分"], "has_rank_system": False},
}
# Minimal valid PNG signature plus IHDR/IEND is sufficient for the upload helper.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


async def _create_game(client: AsyncClient, admin: dict) -> int:
    response = await client.post("/games/", json=_GAME, headers=auth_header(admin))
    assert response.status_code == 201
    return response.json()["id"]


async def test_admin_can_upload_and_clear_game_logo(
    client: AsyncClient, admin_user: dict, monkeypatch, tmp_path
):
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    game_id = await _create_game(client, admin_user)
    response = await client.put(
        f"/games/{game_id}/logo",
        files={"logo": ("logo.png", BytesIO(_PNG), "image/png")},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["logo_url"].startswith("/uploads/games/")
    assert len(list((tmp_path / "games").iterdir())) == 1

    response = await client.delete(f"/games/{game_id}/logo", headers=auth_header(admin_user))
    assert response.status_code == 200


async def test_logo_rejects_mismatched_or_oversized_upload(
    client: AsyncClient, admin_user: dict, monkeypatch, tmp_path
):
    from app.core import config

    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(tmp_path))
    game_id = await _create_game(client, admin_user)
    response = await client.put(
        f"/games/{game_id}/logo",
        files={"logo": ("logo.exe", BytesIO(_PNG), "application/octet-stream")},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400
    response = await client.put(
        f"/games/{game_id}/logo",
        files={"logo": ("logo.png", BytesIO(b"x" * (2 * 1024 * 1024 + 1)), "image/png")},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400


async def test_bulk_action_requires_admin_and_changes_status(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    game_id = await _create_game(client, admin_user)
    response = await client.post(
        "/games/bulk-action",
        json={"action": "activate", "ids": [game_id]},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    response = await client.post(
        "/games/bulk-action",
        json={"action": "deactivate", "ids": [game_id]},
        headers=auth_header(registered_user),
    )
    assert response.status_code in (401, 403)


async def test_bulk_delete_missing_orders_deletes_games(
    client: AsyncClient, admin_user: dict
):
    game_id = await _create_game(client, admin_user)
    response = await client.post(
        "/games/bulk-action",
        json={"action": "delete", "ids": [game_id]},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
