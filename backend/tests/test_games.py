"""Game catalog tests: default deactivation + admin-only game management.

业务背景：所有 seed 游戏默认下架（is_active=0），老板（管理员）在后台
自行添加并上架要用的游戏。对外列表（用户/打手视角）只返回上架游戏，
管理员视角（/admin/games 与 /games 管理员鉴权）能看到全部。
"""

from httpx import AsyncClient
from tests.conftest import auth_header

_GAME_PAYLOAD = {
    "name": "三角洲行动",
    "english_name": "Delta Force",
    "category": "FPS",
    "platform": "BOTH",
    "service_template": {
        "service_types": ["代练上分", "陪玩", "教学"],
        "has_rank_system": True,
        "rank_tiers": ["青铜", "白银", "黄金", "铂金", "钻石", "大师", "宗师"],
        "servers": ["微信区", "QQ区", "PC官服"],
        "roles": ["突击", "侦察", "工程", "支援"],
        "custom_fields": [],
    },
    "description": "三角洲行动热门服务专区",
}


async def test_public_list_hides_inactive_games(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    """全下架后：用户列表不返回游戏；管理员视角可见全部。"""
    # 空库：用户列表为 0
    resp = await client.get("/games/", headers=auth_header(registered_user))
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    # 管理员创建游戏（默认下架）
    resp = await client.post(
        "/admin/games", json=_GAME_PAYLOAD, headers=auth_header(admin_user)
    )
    assert resp.status_code == 201
    game = resp.json()
    assert game["is_active"] is False
    game_id = game["id"]

    # 用户视角仍看不到
    resp = await client.get("/games/", headers=auth_header(registered_user))
    assert resp.json()["total"] == 0

    # 管理员视角（/admin/games 全量列表）可见
    resp = await client.get("/admin/games", headers=auth_header(admin_user))
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["id"] == game_id

    # 管理员上架后，用户列表可见
    resp = await client.put(
        f"/admin/games/{game_id}",
        json={"is_active": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    resp = await client.get("/games/", headers=auth_header(registered_user))
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["name"] == "三角洲行动"

    # 再次下架，用户列表恢复为 0
    resp = await client.put(
        f"/admin/games/{game_id}",
        json={"is_active": False},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    resp = await client.get("/games/", headers=auth_header(registered_user))
    assert resp.json()["total"] == 0

    # 单个游戏详情：用户 404，管理员可见
    resp = await client.get(f"/games/{game_id}", headers=auth_header(registered_user))
    assert resp.status_code == 404
    resp = await client.get(f"/games/{game_id}", headers=auth_header(admin_user))
    assert resp.status_code == 200


async def test_admin_game_crud_lifecycle(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    """管理员游戏 CRUD：创建（默认下架）→ 改名/排序 → 删除。"""
    # 创建
    resp = await client.post(
        "/admin/games", json=_GAME_PAYLOAD, headers=auth_header(admin_user)
    )
    assert resp.status_code == 201
    game = resp.json()
    game_id = game["id"]
    assert game["is_active"] is False

    # 更新（改名 + 排序 + 上架）
    resp = await client.put(
        f"/admin/games/{game_id}",
        json={"name": "三角洲行动（焕新）", "sort_order": 5, "is_active": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["name"] == "三角洲行动（焕新）"
    assert updated["sort_order"] == 5
    assert updated["is_active"] is True

    # 删除
    resp = await client.delete(
        f"/admin/games/{game_id}", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200

    # 删除后管理员列表为空，详情 404
    resp = await client.get("/admin/games", headers=auth_header(admin_user))
    assert resp.json()["total"] == 0
    resp = await client.get(f"/games/{game_id}", headers=auth_header(admin_user))
    assert resp.status_code == 404


async def test_non_admin_cannot_manage_games(
    client: AsyncClient, registered_user: dict
):
    """非管理员不能创建/管理游戏（401/403）。"""
    resp = await client.post("/admin/games", json=_GAME_PAYLOAD)
    assert resp.status_code in (401, 403)
    resp = await client.post(
        "/admin/games", json=_GAME_PAYLOAD, headers=auth_header(registered_user)
    )
    assert resp.status_code in (401, 403)
    # 旧接口 /games 亦为管理员权限
    resp = await client.post(
        "/games/", json=_GAME_PAYLOAD, headers=auth_header(registered_user)
    )
    assert resp.status_code in (401, 403)


async def test_legacy_games_create_defaults_to_inactive(
    client: AsyncClient, admin_user: dict
):
    """原有 /games 管理员创建接口：新游戏同样默认下架。"""
    resp = await client.post("/games/", json=_GAME_PAYLOAD, headers=auth_header(admin_user))
    assert resp.status_code == 201
    game = resp.json()
    assert game["is_active"] is False
