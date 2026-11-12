"""全站发单权限总开关 + 用户明确封禁发单（publish_banned）的端到端行为。

口径（2026-11-12 冻结）：
- 非管理员实际能否发单 = not publish_banned and
  (not publish_permission_required or can_publish)；管理员始终可以。
- required=false 时未封禁的非管理员无需已开通权限即可发单、使用黑名单。
- publish_banned 是个人封禁，两种模式下都阻止发单/自助申请；审核通过不会
  清除封禁，只有开通发单权限或显式解除封禁才会清除。
"""

from httpx import AsyncClient
from sqlalchemy import select

from app.models.publish_permission_request import (
    PublishPermissionRequest,
    PublishPermissionStatus,
)
from app.models.user import User
from app.services.publish_permission_service import (
    PUBLISH_BANNED_DETAIL,
    PUBLISH_NOT_GRANTED_DETAIL,
)
from tests.conftest import auth_header

PASSWORD = "GlobalSwitch1"


async def _register(
    client: AsyncClient, make_captcha, email: str, username: str
) -> dict:
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": PASSWORD,
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _get_user(db_session, user_id: int) -> User:
    # 测试 session 是 expire_on_commit=False 且常驻：expire_all 清掉 identity map
    # 里的旧属性，commit 结束上一次读取留下的 REPEATABLE READ 快照，避免看不到
    # API 请求（另一个 session）刚提交的变更。
    db_session.expire_all()
    await db_session.commit()
    result = await db_session.execute(select(User).where(User.id == user_id))
    return result.scalar_one()


async def _set_flags(db_session, user_id: int, **values) -> None:
    user = await _get_user(db_session, user_id)
    for field, value in values.items():
        setattr(user, field, value)
    await db_session.commit()




async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: str) -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "发单权限测试充值"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text


async def _switch(
    client: AsyncClient, admin_user: dict, *, required: bool, **extra
) -> dict:
    resp = await client.put(
        "/admin/site/settings",
        json={"site_name": "平台", "publish_permission_required": required, **extra},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _order_json(price: str = "10.00") -> dict:
    return {
        "game_name": "王者荣耀",
        "title": "发单权限测试订单",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": price,
        "max_claims": 1,
    }


async def _create(client: AsyncClient, user: dict, price: str = "10.00"):
    return await client.post(
        "/orders/create",
        json=_order_json(price),
        headers=auth_header(user),
    )


# ---------------------------------------------------------------------------
# 默认 required=True
# ---------------------------------------------------------------------------


async def test_default_required_blocks_ungranted_and_banned_but_allows_admin(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict, make_captcha
):
    ungranted_id = registered_user["user"]["id"]
    await _set_flags(db_session, ungranted_id, can_publish=False, publish_banned=False)

    resp = await _create(client, registered_user)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PUBLISH_NOT_GRANTED_DETAIL

    # /auth/me 快照：已开通=false、未封禁、实际不可发单
    me = await client.get("/auth/me", headers=auth_header(registered_user))
    assert me.status_code == 200
    body = me.json()
    assert body["can_publish"] is False
    assert body["publish_banned"] is False
    assert body["can_publish_effective"] is False

    banned = await _register(
        client, make_captcha, "gs.banned@example.com", "GsBanned"
    )
    banned_id = banned["user"]["id"]
    # can_publish 仍为 true，但明确封禁后一律不能发单
    await _set_flags(db_session, banned_id, can_publish=True, publish_banned=True)
    resp = await _create(client, banned)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PUBLISH_BANNED_DETAIL

    # 管理员不受总开关/个人标记影响
    resp = await _create(client, admin_user, price="500.00")
    assert resp.status_code == 201, resp.text
    assert admin_user["user"]["can_publish_effective"] is True


# ---------------------------------------------------------------------------
# 全站开放 required=False
# ---------------------------------------------------------------------------


async def test_global_open_allows_ungranted_but_still_blocks_banned(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict, make_captcha
):
    ungranted_id = registered_user["user"]["id"]
    await _set_flags(db_session, ungranted_id, can_publish=False, publish_banned=False)
    await _fund(client, admin_user, registered_user, "100.00")

    banned = await _register(
        client, make_captcha, "gs.open.banned@example.com", "GsOpenBanned"
    )
    await _set_flags(
        db_session, banned["user"]["id"], can_publish=True, publish_banned=True
    )

    # 显式 False 必须生效，并公开可读
    setting = await _switch(client, admin_user, required=False)
    assert setting["publish_permission_required"] is False
    public = await client.get("/site/settings")
    assert public.json()["publish_permission_required"] is False

    # 未开通、未封禁：全站开放时可以直接发单
    resp = await _create(client, registered_user)
    assert resp.status_code == 201, resp.text

    # 被封禁：仍然 403，且文案区分封禁
    resp = await _create(client, banned, price="10.00")
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PUBLISH_BANNED_DETAIL

    # 「我的发单权限」状态：raw can_publish 仍为 false，但实际可用
    mine = await client.get(
        "/publish-permission/mine", headers=auth_header(registered_user)
    )
    assert mine.status_code == 200
    body = mine.json()
    assert body["can_publish"] is False
    assert body["publish_banned"] is False
    assert body["required"] is False
    assert body["can_publish_effective"] is True

    banned_mine = await client.get(
        "/publish-permission/mine", headers=auth_header(banned)
    )
    assert banned_mine.json()["publish_banned"] is True
    assert banned_mine.json()["can_publish_effective"] is False

    # 全站开放时申请没有意义
    resp = await client.post(
        "/publish-permission/apply", headers=auth_header(registered_user)
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "当前全站开放发单，无需申请"

    # 黑名单：可发单口径，未开通用户 200、被封禁用户 403
    ok = await client.get("/blacklist/mine", headers=auth_header(registered_user))
    assert ok.status_code == 200, ok.text
    blocked = await client.get("/blacklist/mine", headers=auth_header(banned))
    assert blocked.status_code == 403, blocked.text


# ---------------------------------------------------------------------------
# 开关切回 required=True
# ---------------------------------------------------------------------------


async def test_switch_back_to_required_blocks_ungranted_immediately(
    client: AsyncClient, db_session, registered_user: dict, booster_user: dict, admin_user: dict
):
    ungranted_id = registered_user["user"]["id"]
    granted_id = booster_user["user"]["id"]
    await _set_flags(db_session, ungranted_id, can_publish=False, publish_banned=False)
    await _set_flags(db_session, granted_id, can_publish=True, publish_banned=False)
    await _fund(client, admin_user, registered_user, "100.00")
    await _fund(client, admin_user, booster_user, "100.00")

    await _switch(client, admin_user, required=False)
    assert (await _create(client, registered_user)).status_code == 201

    # 改回 true 后立即生效，无需等待缓存（站点设置每次请求实时读取）
    await _switch(client, admin_user, required=True)
    resp = await _create(client, registered_user)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PUBLISH_NOT_GRANTED_DETAIL

    # 已开通用户不受影响
    resp = await _create(client, booster_user)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# 注册默认权限 false 的新用户跟随全站开关
# ---------------------------------------------------------------------------


async def test_new_user_with_registration_default_off_follows_global_switch(
    client: AsyncClient, admin_user: dict, make_captcha
):
    await _switch(
        client,
        admin_user,
        required=False,
        registration_default_can_publish=False,
    )

    new_user = await _register(
        client, make_captcha, "gs.default.off@example.com", "GsDefaultOff"
    )
    assert new_user["user"]["can_publish"] is False
    assert new_user["user"]["publish_banned"] is False
    assert new_user["user"]["can_publish_effective"] is True

    await _fund(client, admin_user, new_user, "100.00")
    resp = await _create(client, new_user)
    assert resp.status_code == 201, resp.text

    await _switch(client, admin_user, required=True)
    resp = await _create(client, new_user)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PUBLISH_NOT_GRANTED_DETAIL


# ---------------------------------------------------------------------------
# 封禁与申请/审核
# ---------------------------------------------------------------------------


async def test_banned_user_apply_and_approve_while_banned(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _set_flags(db_session, user_id, can_publish=False, publish_banned=False)

    applied = await client.post(
        "/publish-permission/apply",
        json={"reason": "希望能发单"},
        headers=auth_header(registered_user),
    )
    assert applied.status_code == 200, applied.text
    request_id = applied.json()["id"]
    assert applied.json()["can_publish"] is False
    assert applied.json()["publish_banned"] is False

    # 审核前被封禁：approve 必须被拒，申请保持待审核且不会误开通
    resp = await client.post(
        f"/admin/users/{user_id}/restrictions",
        json={"publish_banned": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["publish_banned"] is True

    review = await client.post(
        f"/admin/publish-permission/requests/{request_id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert review.status_code == 400, review.text
    assert "解除封禁" in review.json()["detail"]

    db_session.expire_all()
    request = (
        await db_session.execute(
            select(PublishPermissionRequest).where(
                PublishPermissionRequest.id == request_id
            )
        )
    ).scalar_one()
    assert request.status == PublishPermissionStatus.PENDING
    user = await _get_user(db_session, user_id)
    assert user.can_publish is False
    assert user.publish_banned is True

    # 封禁期间自助申请也直接 403
    apply_banned = await client.post(
        "/publish-permission/apply", headers=auth_header(registered_user)
    )
    assert apply_banned.status_code == 403, apply_banned.text
    assert apply_banned.json()["detail"] == PUBLISH_BANNED_DETAIL

    # 解除封禁后可正常审核通过；approve 本身不清除封禁标记
    resp = await client.post(
        f"/admin/users/{user_id}/restrictions",
        json={"publish_banned": False},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    review = await client.post(
        f"/admin/publish-permission/requests/{request_id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert review.status_code == 200, review.text
    user = await _get_user(db_session, user_id)
    assert user.can_publish is True
    assert user.publish_banned is False


# ---------------------------------------------------------------------------
# 批量/单人/ PATCH 的封禁联动
# ---------------------------------------------------------------------------


async def test_bulk_revoke_keeps_ban_and_grant_clears_it(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _set_flags(db_session, user_id, can_publish=True, publish_banned=True)

    # 批量撤销发单权限：只改 can_publish，保留封禁标记
    resp = await client.post(
        "/admin/users/bulk-publish-permission",
        json={"scope": "selected", "user_ids": [user_id], "can_publish": False},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    user = await _get_user(db_session, user_id)
    assert user.can_publish is False
    assert user.publish_banned is True

    # 批量开通：同时清除封禁，否则开通对封禁用户无效
    resp = await client.post(
        "/admin/users/bulk-publish-permission",
        json={"scope": "selected", "user_ids": [user_id], "can_publish": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    user = await _get_user(db_session, user_id)
    assert user.can_publish is True
    assert user.publish_banned is False


async def test_restrictions_and_patch_ban_semantics(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    headers = auth_header(admin_user)
    await _set_flags(db_session, user_id, can_publish=True, publish_banned=False)

    # 单独封禁：can_publish 不动
    resp = await client.post(
        f"/admin/users/{user_id}/restrictions",
        json={"publish_banned": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_publish"] is True
    assert resp.json()["publish_banned"] is True

    # 只撤销开通：保留封禁
    resp = await client.post(
        f"/admin/users/{user_id}/restrictions",
        json={"can_publish": False},
        headers=headers,
    )
    assert resp.json()["can_publish"] is False
    assert resp.json()["publish_banned"] is True

    # 单独解封：can_publish 仍为 false
    resp = await client.post(
        f"/admin/users/{user_id}/restrictions",
        json={"publish_banned": False},
        headers=headers,
    )
    assert resp.json()["publish_banned"] is False
    assert resp.json()["can_publish"] is False

    # 开通发单权限：同时清除封禁
    await _set_flags(db_session, user_id, publish_banned=True)
    resp = await client.post(
        f"/admin/users/{user_id}/restrictions",
        json={"can_publish": True},
        headers=headers,
    )
    assert resp.json()["can_publish"] is True
    assert resp.json()["publish_banned"] is False

    # PATCH can_publish=true 同样清除封禁；PATCH false 保留封禁
    await _set_flags(db_session, user_id, can_publish=False, publish_banned=True)
    resp = await client.patch(
        f"/admin/users/{user_id}", json={"can_publish": False}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["publish_banned"] is True

    resp = await client.patch(
        f"/admin/users/{user_id}", json={"can_publish": True}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_publish"] is True
    assert resp.json()["publish_banned"] is False

    # 直接 PATCH publish_banned 也应单独生效
    resp = await client.patch(
        f"/admin/users/{user_id}", json={"publish_banned": True}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["publish_banned"] is True
    resp = await client.patch(
        f"/admin/users/{user_id}", json={"publish_banned": False}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["publish_banned"] is False

    # 开通与封禁同时为 true 语义冲突：PATCH / restrictions 都拒绝，
    # 不能让字段应用顺序决定最终状态。
    conflict_patch = await client.patch(
        f"/admin/users/{user_id}",
        json={"can_publish": True, "publish_banned": True},
        headers=headers,
    )
    assert conflict_patch.status_code == 400, conflict_patch.text
    conflict_restrictions = await client.post(
        f"/admin/users/{user_id}/restrictions",
        json={"can_publish": True, "publish_banned": True},
        headers=headers,
    )
    assert conflict_restrictions.status_code == 400, conflict_restrictions.text


async def test_admin_bypasses_publish_ban(
    client: AsyncClient, db_session, admin_user: dict
):
    """管理员始终可发单：即使历史数据误写了 publish_banned=true 也不受影响。"""
    admin_id = admin_user["user"]["id"]
    await _set_flags(db_session, admin_id, can_publish=True, publish_banned=True)
    resp = await _create(client, admin_user, price="500.00")
    assert resp.status_code == 201, resp.text


async def test_bulk_all_non_admin_preserves_bans_and_skips_inactive(
    client: AsyncClient,
    db_session,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
):
    """全量批量只作用启用中的非管理员；撤销不解除封禁，开通才解除。"""
    banned_id = registered_user["user"]["id"]
    inactive_id = booster_user["user"]["id"]
    await _set_flags(db_session, banned_id, can_publish=True, publish_banned=True)
    await _set_flags(
        db_session, inactive_id, is_active=False, can_publish=True, publish_banned=False
    )
    headers = auth_header(admin_user)

    revoked = await client.post(
        "/admin/users/bulk-publish-permission",
        json={"scope": "all_non_admin", "can_publish": False},
        headers=headers,
    )
    assert revoked.status_code == 200, revoked.text
    # `_get_user` 内部会 expire_all：先把第一人的属性读成普通值，再查第二人，
    # 否则第二次 expire_all 会让第一个 ORM 对象在断言时触发懒加载。
    banned = await _get_user(db_session, banned_id)
    banned_flags = (banned.can_publish, banned.publish_banned)
    inactive = await _get_user(db_session, inactive_id)
    inactive_flags = (inactive.can_publish, inactive.publish_banned)
    assert banned_flags == (False, True)
    # 禁用用户不在全量作用范围，不被误改
    assert inactive_flags == (True, False)

    granted = await client.post(
        "/admin/users/bulk-publish-permission",
        json={"scope": "all_non_admin", "can_publish": True},
        headers=headers,
    )
    assert granted.status_code == 200, granted.text
    banned = await _get_user(db_session, banned_id)
    banned_flags = (banned.can_publish, banned.publish_banned)
    inactive = await _get_user(db_session, inactive_id)
    inactive_flags = (inactive.can_publish, inactive.publish_banned)
    assert banned_flags == (True, False)
    assert inactive_flags == (True, False)


# ---------------------------------------------------------------------------
# 后台列表回显与筛选
# ---------------------------------------------------------------------------


async def test_admin_user_list_exposes_and_filters_publish_banned(
    client: AsyncClient, db_session, registered_user: dict, booster_user: dict, admin_user: dict
):
    banned_id = registered_user["user"]["id"]
    await _set_flags(db_session, banned_id, publish_banned=True)

    resp = await client.get("/admin/users", headers=auth_header(admin_user))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert all("publish_banned" in item for item in items)
    by_id = {item["id"]: item for item in items}
    assert by_id[banned_id]["publish_banned"] is True

    filtered = await client.get(
        "/admin/users",
        params={"publish_banned": "true"},
        headers=auth_header(admin_user),
    )
    assert filtered.status_code == 200, filtered.text
    data = filtered.json()
    assert banned_id in {item["id"] for item in data["items"]}
    assert all(item["publish_banned"] is True for item in data["items"])

    not_banned = await client.get(
        "/admin/users",
        params={"publish_banned": "false"},
        headers=auth_header(admin_user),
    )
    assert not_banned.status_code == 200
    assert banned_id not in {item["id"] for item in not_banned.json()["items"]}
