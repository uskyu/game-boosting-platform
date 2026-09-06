"""用户名修改冷却测试：普通用户 90 天一次，管理员后台不受限。"""

import pytest
from httpx import AsyncClient

from tests.conftest import auth_header

COOLDOWN_HINT = "90 天"


async def _rename(client: AsyncClient, user: dict, username: str):
    return await client.put("/auth/me", json={"username": username}, headers=auth_header(user))


@pytest.mark.asyncio
async def test_first_self_rename_allowed_then_cooldown_blocks(client, registered_user):
    response = await _rename(client, registered_user, "冷却测试一号")
    assert response.status_code == 200
    assert response.json()["username"] == "冷却测试一号"
    assert response.json()["username_changed_at"] is not None

    blocked = await _rename(client, registered_user, "冷却测试二号")
    assert blocked.status_code == 400
    assert COOLDOWN_HINT in blocked.json()["detail"]


@pytest.mark.asyncio
async def test_other_fields_editable_during_cooldown(client, registered_user):
    assert (await _rename(client, registered_user, "冷却测试三号")).status_code == 200
    bio = await client.put("/auth/me", json={"bio": "冷却期内仍可改简介"}, headers=auth_header(registered_user))
    assert bio.status_code == 200
    assert bio.json()["bio"] == "冷却期内仍可改简介"


@pytest.mark.asyncio
async def test_admin_rename_bypasses_cooldown_and_resets_window(client, registered_user, admin_user):
    assert (await _rename(client, registered_user, "冷却测试四号")).status_code == 200
    admin_set = await client.patch(
        f"/admin/users/{registered_user['user']['id']}",
        json={"username": "管理员改名一号"},
        headers=auth_header(admin_user),
    )
    assert admin_set.status_code == 200
    assert admin_set.json()["username"] == "管理员改名一号"

    # 管理员改名重置冷却窗口：用户立刻自助改名仍应被拦
    still_blocked = await _rename(client, registered_user, "冷却测试五号")
    assert still_blocked.status_code == 400
    assert COOLDOWN_HINT in still_blocked.json()["detail"]
