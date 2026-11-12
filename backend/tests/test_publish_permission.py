"""发单权限：注册默认值、自助申请/审核、批量调整与列表过滤。"""

from httpx import AsyncClient
from sqlalchemy import select

from app.models.notification import Notification, NotificationType
from app.models.publish_permission_request import (
    PublishPermissionRequest,
    PublishPermissionStatus,
)
from app.models.site_setting import SiteSetting
from app.models.user import User
from tests.conftest import auth_header


async def _register(client: AsyncClient, make_captcha, email: str, username: str) -> dict:
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "StrongPass1",
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _get_user(db_session, user_id: int) -> User:
    db_session.expire_all()
    result = await db_session.execute(select(User).where(User.id == user_id))
    return result.scalar_one()


async def _set_user_flags(db_session, user_id: int, **values) -> None:
    user = await _get_user(db_session, user_id)
    for field, value in values.items():
        setattr(user, field, value)
    await db_session.commit()


async def _get_request(db_session, request_id: int) -> PublishPermissionRequest:
    db_session.expire_all()
    result = await db_session.execute(
        select(PublishPermissionRequest).where(PublishPermissionRequest.id == request_id)
    )
    return result.scalar_one()


async def _apply(client: AsyncClient, user_data: dict, reason: str | None = None) -> dict:
    """不带 reason 时不发送请求体，覆盖 body 可选的契约。"""
    kwargs = {"headers": auth_header(user_data)}
    if reason is not None:
        kwargs["json"] = {"reason": reason}
    resp = await client.post("/publish-permission/apply", **kwargs)
    return resp


# ---------------------------------------------------------------------------
# 注册默认值 / 站点设置 / env 初始化
# ---------------------------------------------------------------------------


async def test_registration_defaults_allow_publish_and_accept(client: AsyncClient, make_captcha):
    data = await _register(client, make_captcha, "default_on@example.com", "DefaultOn")
    assert data["user"]["can_publish"] is True
    assert data["user"]["can_accept"] is True

    me = await client.get("/auth/me", headers=auth_header(data))
    assert me.status_code == 200
    assert me.json()["can_publish"] is True
    assert me.json()["can_accept"] is True


async def test_registration_defaults_off_only_affect_new_users(
    client: AsyncClient, db_session, make_captcha
):
    old_user = await _register(client, make_captcha, "old_user@example.com", "OldUser")
    assert old_user["user"]["can_publish"] is True

    setting = (
        await db_session.execute(select(SiteSetting).where(SiteSetting.id == 1))
    ).scalar_one()
    setting.registration_default_can_publish = False
    setting.registration_default_can_accept = False
    await db_session.commit()

    new_user = await _register(client, make_captcha, "new_user@example.com", "NewUser")
    assert new_user["user"]["can_publish"] is False
    assert new_user["user"]["can_accept"] is False

    # 老用户不回溯
    old_me = await client.get("/auth/me", headers=auth_header(old_user))
    assert old_me.json()["can_publish"] is True
    assert old_me.json()["can_accept"] is True


async def test_admin_settings_toggle_registration_defaults(
    client: AsyncClient, admin_user: dict
):
    headers = auth_header(admin_user)
    resp = await client.put(
        "/admin/site/settings",
        json={
            "site_name": "平台",
            "registration_default_can_publish": False,
            "registration_default_can_accept": False,
            "publish_permission_apply_enabled": False,
            "publish_permission_required": False,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    # 显式 False 必须生效（不是被当成未传）
    assert resp.json()["registration_default_can_publish"] is False
    assert resp.json()["registration_default_can_accept"] is False
    assert resp.json()["publish_permission_apply_enabled"] is False
    assert resp.json()["publish_permission_required"] is False

    public = await client.get("/site/settings")
    assert public.json()["registration_default_can_publish"] is False
    assert public.json()["registration_default_can_accept"] is False
    assert public.json()["publish_permission_apply_enabled"] is False
    assert public.json()["publish_permission_required"] is False

    # 不传时保持原值
    resp = await client.put(
        "/admin/site/settings",
        json={"site_name": "平台改名"},
        headers=headers,
    )
    assert resp.json()["registration_default_can_publish"] is False
    assert resp.json()["registration_default_can_accept"] is False
    assert resp.json()["publish_permission_apply_enabled"] is False
    assert resp.json()["publish_permission_required"] is False


async def test_ensure_site_settings_uses_env_once(
    db_session, monkeypatch
):
    from app.core import config
    from app.services.site_setting_service import ensure_site_settings

    setting = (
        await db_session.execute(select(SiteSetting).where(SiteSetting.id == 1))
    ).scalar_one()
    await db_session.delete(setting)
    await db_session.commit()

    monkeypatch.setattr(config.settings, "REGISTRATION_DEFAULT_CAN_PUBLISH", False)
    monkeypatch.setattr(config.settings, "REGISTRATION_DEFAULT_CAN_ACCEPT", True)
    monkeypatch.setattr(config.settings, "PUBLISH_PERMISSION_APPLY_ENABLED", False)
    monkeypatch.setattr(config.settings, "PUBLISH_PERMISSION_REQUIRED", False)

    created = await ensure_site_settings(db_session)
    await db_session.commit()
    assert created.id == 1
    assert created.site_name == "游戏服务平台"
    assert created.registration_default_can_publish is False
    assert created.registration_default_can_accept is True
    assert created.publish_permission_apply_enabled is False
    assert created.publish_permission_required is False

    # 管理员改过之后，二次启动不得被 env 覆盖
    created.registration_default_can_publish = True
    created.publish_permission_apply_enabled = True
    created.publish_permission_required = True
    await db_session.commit()

    again = await ensure_site_settings(db_session)
    await db_session.commit()
    assert again.registration_default_can_publish is True
    assert again.publish_permission_apply_enabled is True
    assert again.publish_permission_required is True
    assert again.registration_default_can_accept is True


# ---------------------------------------------------------------------------
# 申请 / 撤回
# ---------------------------------------------------------------------------


async def test_apply_and_duplicate_pending_conflict(
    client: AsyncClient, db_session, registered_user: dict
):
    user_id = registered_user["user"]["id"]
    await _set_user_flags(db_session, user_id, can_publish=False)

    mine = await client.get("/publish-permission/mine", headers=auth_header(registered_user))
    assert mine.status_code == 200
    assert mine.json()["can_publish"] is False
    assert mine.json()["publish_banned"] is False
    assert mine.json()["required"] is True
    assert mine.json()["can_publish_effective"] is False
    assert mine.json()["apply_enabled"] is True
    assert mine.json()["latest"] is None
    assert mine.json()["has_pending"] is False

    resp = await _apply(client, registered_user, "  想发单  ")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["reason"] == "想发单"
    assert body["history_count"] == 1
    assert body["can_publish"] is False
    assert body["publish_banned"] is False

    mine = await client.get("/publish-permission/mine", headers=auth_header(registered_user))
    assert mine.json()["has_pending"] is True
    assert mine.json()["latest"]["id"] == body["id"]

    duplicate = await _apply(client, registered_user, "再来一次")
    assert duplicate.status_code == 409
    assert "待审核" in duplicate.json()["detail"]


async def test_apply_rejected_when_eligible_admin_or_apply_disabled(
    client: AsyncClient, db_session, registered_user: dict, booster_user: dict, admin_user: dict
):
    # 已具备发单权限
    resp = await _apply(client, registered_user)
    assert resp.status_code == 400
    assert "已具备发单权限" in resp.json()["detail"]

    # 管理员无需申请（管理员默认 can_publish=True，也必须先报管理员）
    resp = await _apply(client, admin_user)
    assert resp.status_code == 400
    assert "管理员无需申请" in resp.json()["detail"]

    # 后台关闭自助申请
    booster_id = booster_user["user"]["id"]
    await _set_user_flags(db_session, booster_id, can_publish=False)
    setting = (
        await db_session.execute(select(SiteSetting).where(SiteSetting.id == 1))
    ).scalar_one()
    setting.publish_permission_apply_enabled = False
    await db_session.commit()

    resp = await _apply(client, booster_user)
    assert resp.status_code == 400
    assert "未开启自助申请" in resp.json()["detail"]


async def test_withdraw_own_pending_only(
    client: AsyncClient, db_session, registered_user: dict, make_captcha
):
    user_id = registered_user["user"]["id"]
    await _set_user_flags(db_session, user_id, can_publish=False)
    applied = await _apply(client, registered_user, "申请理由")
    assert applied.status_code == 200
    request_id = applied.json()["id"]

    other = await _register(client, make_captcha, "withdraw_other@example.com", "WithdrawOther")
    resp = await client.post(
        f"/publish-permission/{request_id}/withdraw",
        headers=auth_header(other),
    )
    assert resp.status_code == 404

    resp = await client.post(
        f"/publish-permission/{request_id}/withdraw",
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "WITHDRAWN"
    assert body["resolved_at"] is not None

    # 已撤回不能再撤回
    resp = await client.post(
        f"/publish-permission/{request_id}/withdraw",
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 管理员审核
# ---------------------------------------------------------------------------


async def test_review_approve_grants_publish_and_notifies(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _set_user_flags(db_session, user_id, can_publish=False)
    applied = await _apply(client, registered_user, "希望发单")
    assert applied.status_code == 200
    request_id = applied.json()["id"]

    resp = await client.post(
        f"/admin/publish-permission/requests/{request_id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "APPROVED"
    assert body["reviewed_by"] == admin_user["user"]["id"]
    assert body["reviewed_by_name"] == "TestAdmin"

    request = await _get_request(db_session, request_id)
    assert request.status == PublishPermissionStatus.APPROVED
    assert request.reviewed_at is not None
    assert request.resolved_at is not None

    # 权限已下发
    me = await client.get("/auth/me", headers=auth_header(registered_user))
    assert me.json()["can_publish"] is True
    assert me.json()["publish_banned"] is False
    assert me.json()["can_publish_effective"] is True

    # 通知已写入（复用 APPLICATION_APPROVED）
    notifications = (
        await db_session.execute(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.type == NotificationType.APPLICATION_APPROVED,
                Notification.ref_id == request_id,
            )
        )
    ).scalars().all()
    assert notifications
    assert notifications[0].title == "发单权限申请已通过"
    assert notifications[0].link == "/publish-permission"

    # 非 PENDING 再审 -> 409
    again = await client.post(
        f"/admin/publish-permission/requests/{request_id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert again.status_code == 409


async def test_review_reject_requires_reason_and_notifies(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _set_user_flags(db_session, user_id, can_publish=False)
    applied = await _apply(client, registered_user)
    request_id = applied.json()["id"]

    resp = await client.post(
        f"/admin/publish-permission/requests/{request_id}/review",
        json={"action": "reject", "reason": "   "},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400
    assert "驳回必须填写原因" in resp.json()["detail"]

    resp = await client.post(
        f"/admin/publish-permission/requests/{request_id}/review",
        json={"action": "reject", "reason": "资料不足"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert body["decision_reason"] == "资料不足"

    request = await _get_request(db_session, request_id)
    assert request.status == PublishPermissionStatus.REJECTED
    user = await _get_user(db_session, user_id)
    assert user.can_publish is False

    notifications = (
        await db_session.execute(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.type == NotificationType.APPLICATION_REJECTED,
                Notification.ref_id == request_id,
            )
        )
    ).scalars().all()
    assert notifications
    assert notifications[0].title == "发单权限申请已驳回"


async def test_non_admin_cannot_review_or_list(
    client: AsyncClient, registered_user: dict
):
    resp = await client.post(
        "/admin/publish-permission/requests/1/review",
        json={"action": "approve"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403

    resp = await client.get(
        "/admin/publish-permission/requests",
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403


async def test_admin_list_filters_search_and_history_count(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _set_user_flags(db_session, user_id, can_publish=False)

    first = await _apply(client, registered_user, "第一次申请")
    assert first.status_code == 200
    await client.post(
        f"/publish-permission/{first.json()['id']}/withdraw",
        headers=auth_header(registered_user),
    )
    second = await _apply(client, registered_user, "第二次申请")
    assert second.status_code == 200

    resp = await client.get(
        "/admin/publish-permission/requests",
        params={"status": "PENDING", "q": "testuser"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["id"] == second.json()["id"]
    assert item["username"] == "TestUser"
    assert item["history_count"] == 2
    assert item["reason"] == "第二次申请"

    # 后台搜索支持用户 ID：纯数字关键词按 ID 精确命中
    resp = await client.get(
        "/admin/publish-permission/requests",
        params={"status": "PENDING", "q": str(user_id)},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    by_id = resp.json()
    assert by_id["total"] == 1
    assert by_id["items"][0]["id"] == second.json()["id"]
    assert by_id["items"][0]["user_id"] == user_id

    # 审核后按状态检索，带审核人姓名
    await client.post(
        f"/admin/publish-permission/requests/{second.json()['id']}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    resp = await client.get(
        "/admin/publish-permission/requests",
        params={"status": "APPROVED"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["items"][0]["reviewed_by_name"] == "TestAdmin"


# ---------------------------------------------------------------------------
# 批量调整 + 管理员直接开通
# ---------------------------------------------------------------------------


async def test_bulk_selected_skips_admin_and_missing(
    client: AsyncClient, db_session, registered_user: dict, booster_user: dict, admin_user: dict
):
    regular_id = registered_user["user"]["id"]
    booster_id = booster_user["user"]["id"]
    admin_id = admin_user["user"]["id"]

    resp = await client.post(
        "/admin/users/bulk-publish-permission",
        json={
            "scope": "selected",
            "user_ids": [regular_id, booster_id, admin_id, 999999],
            "can_publish": False,
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 2, "skipped": 2}

    assert (await _get_user(db_session, regular_id)).can_publish is False
    assert (await _get_user(db_session, booster_id)).can_publish is False
    assert (await _get_user(db_session, admin_id)).can_publish is True


async def test_bulk_all_non_admin_auto_approves_pending(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _set_user_flags(db_session, user_id, can_publish=False)
    applied = await _apply(client, registered_user, "批量开通")
    assert applied.status_code == 200
    request_id = applied.json()["id"]

    resp = await client.post(
        "/admin/users/bulk-publish-permission",
        json={"scope": "all_non_admin", "can_publish": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 1, "skipped": 0}

    user = await _get_user(db_session, user_id)
    assert user.can_publish is True
    request = await _get_request(db_session, request_id)
    assert request.status == PublishPermissionStatus.APPROVED
    assert request.decision_reason == "管理员直接开通"
    assert request.reviewed_by == admin_user["user"]["id"]
    assert request.resolved_at is not None

    admin = await _get_user(db_session, admin_user["user"]["id"])
    assert admin.can_publish is True


async def test_bulk_publish_permission_preview_counts(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict
):
    # 普通用户无权查看后台预览
    resp = await client.get(
        "/admin/users/bulk-publish-permission/preview",
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403

    user_id = registered_user["user"]["id"]
    await _set_user_flags(db_session, user_id, can_publish=False)
    applied = await _apply(client, registered_user, "预览统计")
    assert applied.status_code == 200

    resp = await client.get(
        "/admin/users/bulk-publish-permission/preview",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    # 管理员不计入 eligible；此时只有 registered_user 是启用中的非管理员
    assert resp.json() == {"eligible": 1, "pending": 1}


async def test_bulk_selected_requires_ids(client: AsyncClient, admin_user: dict):
    resp = await client.post(
        "/admin/users/bulk-publish-permission",
        json={"scope": "selected", "user_ids": [], "can_publish": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 422


async def test_restrictions_grant_auto_approves_pending(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _set_user_flags(db_session, user_id, can_publish=False)
    applied = await _apply(client, registered_user)
    request_id = applied.json()["id"]

    resp = await client.post(
        f"/admin/users/{user_id}/restrictions",
        json={"can_publish": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_publish"] is True

    request = await _get_request(db_session, request_id)
    assert request.status == PublishPermissionStatus.APPROVED
    assert request.decision_reason == "管理员直接开通"


async def test_admin_patch_grant_auto_approves_pending(
    client: AsyncClient, db_session, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _set_user_flags(db_session, user_id, can_publish=False)
    applied = await _apply(client, registered_user)
    request_id = applied.json()["id"]

    resp = await client.patch(
        f"/admin/users/{user_id}",
        json={"can_publish": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    request = await _get_request(db_session, request_id)
    assert request.status == PublishPermissionStatus.APPROVED


# ---------------------------------------------------------------------------
# 后台列表过滤
# ---------------------------------------------------------------------------


async def test_admin_user_list_filters_by_can_publish(
    client: AsyncClient, db_session, registered_user: dict, booster_user: dict, admin_user: dict
):
    regular_id = registered_user["user"]["id"]
    booster_id = booster_user["user"]["id"]
    await _set_user_flags(db_session, regular_id, can_publish=False)
    await _set_user_flags(db_session, booster_id, can_publish=False)

    resp = await client.get(
        "/admin/users",
        params={"can_publish": "false"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 2
    assert {item["id"] for item in data["items"]} >= {regular_id, booster_id}
    assert all(item["can_publish"] is False for item in data["items"])
    # 未开通不是封禁：publish_banned 应保持 false，且响应带该字段
    assert all(item["publish_banned"] is False for item in data["items"])

    resp = await client.get(
        "/admin/users",
        params={"can_publish": "true"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert regular_id not in ids and booster_id not in ids
