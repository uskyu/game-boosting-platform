"""同时在线设备上限：登录淘汰、会话吊销与 WebSocket 断开。

覆盖需求（2026-10-22）：
1. max=1：B 登录后 A 的 access 立即 401，A 会话 revoked_reason=device_limit；
2. max=2：第三次登录踢掉最早登录的一台；
3. 同一 device_id 重复登录：不新增会话行，旧令牌失效、新令牌可用；
4. max=0：不限设备，多次登录都不踢；
5. logout：当前 access 与 refresh 立即 401；
6. 被踢设备的 refresh 401；
7. 改密码：当前设备用响应里的新令牌继续可用，其他设备 401；
8. WebSocket：超限设备按 4002 关闭（单元测试，原因见文件末尾说明）；
9. 管理员重置密码 / 禁用账号后，旧令牌 401。

另外锁住两个容易漏掉的点：
- 上限从大改小（需要一次淘汰多台）时不能因为边遍历边删而漏踢；
- 改动前签发、没有 sid 声明的老令牌必须 401（老板要求的自动掉线）。
"""

from httpx import AsyncClient
from sqlalchemy import select, update

from app.api.endpoints import auth as auth_module
from app.core.security import create_access_token, create_refresh_token, verify_token
from app.models.site_setting import SiteSetting
from app.models.user_session import UserSession
from app.services import user_session_service
from app.services.connection_manager import ConnectionManager
from app.services.user_session_service import WS_CLOSE_DEVICE_LIMIT
from tests.conftest import auth_header

PASSWORD = "OnlinePass1"
NEW_PASSWORD = "ChangedPass9"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _set_max_devices(db_session, value: int) -> None:
    """修改单行站点设置（conftest 默认播种 max_online_devices=0）。"""
    await db_session.execute(
        update(SiteSetting)
        .where(SiteSetting.id == 1)
        .values(max_online_devices=value)
    )
    await db_session.commit()


async def _register(
    client: AsyncClient,
    make_captcha,
    *,
    email: str = "online@example.com",
    username: str = "OnlineUser",
    device_id: str | None = None,
) -> dict:
    payload = {
        "email": email,
        "username": username,
        "password": PASSWORD,
        **make_captcha(),
    }
    if device_id is not None:
        payload["device_id"] = device_id
    resp = await client.post("/auth/register", json=payload)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _login(
    client: AsyncClient,
    email: str,
    *,
    password: str = PASSWORD,
    device_id: str | None = None,
) -> dict:
    payload = {"email": email, "password": password}
    if device_id is not None:
        payload["device_id"] = device_id
    resp = await client.post("/auth/login", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _me(client: AsyncClient, tokens: dict):
    return await client.get("/auth/me", headers=auth_header(tokens))


async def _refresh(client: AsyncClient, tokens: dict):
    return await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )


async def _sessions(db_session, user_id: int) -> list[UserSession]:
    """读取该用户的全部会话行（先结束旧快照事务，避免看到过期数据）。"""
    await db_session.commit()
    result = await db_session.execute(
        select(UserSession)
        .where(UserSession.user_id == user_id)
        .order_by(UserSession.id)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# 1. max=1：第二台设备登录踢掉第一台
# ---------------------------------------------------------------------------


async def test_max_one_kicks_previous_device(
    client: AsyncClient, db_session, make_captcha
):
    await _set_max_devices(db_session, 1)

    a = await _register(client, make_captcha, device_id="device-a")
    assert (await _me(client, a)).status_code == 200

    b = await _login(client, a["user"]["email"], device_id="device-b")

    # A 的 access 立即失效（会话已吊销），B 正常
    assert (await _me(client, a)).status_code == 401
    assert (await _me(client, b)).status_code == 200

    rows = await _sessions(db_session, a["user"]["id"])
    assert len(rows) == 2
    revoked = [row for row in rows if row.revoked_at is not None]
    assert len(revoked) == 1
    assert revoked[0].device_id == "device-a"
    assert revoked[0].revoked_reason == "device_limit"


# ---------------------------------------------------------------------------
# 2. max=2：第三台登录踢掉最早的一台
# ---------------------------------------------------------------------------


async def test_max_two_kicks_oldest_on_third_login(
    client: AsyncClient, db_session, make_captcha
):
    await _set_max_devices(db_session, 2)

    a = await _register(client, make_captcha, device_id="device-a")
    b = await _login(client, a["user"]["email"], device_id="device-b")
    c = await _login(client, a["user"]["email"], device_id="device-c")

    assert (await _me(client, a)).status_code == 401  # 最早登录的被淘汰
    assert (await _me(client, b)).status_code == 200
    assert (await _me(client, c)).status_code == 200

    rows = await _sessions(db_session, a["user"]["id"])
    revoked = [row for row in rows if row.revoked_at is not None]
    assert [row.device_id for row in revoked] == ["device-a"]
    assert revoked[0].revoked_reason == "device_limit"


# ---------------------------------------------------------------------------
# 3. 相同 device_id 重复登录：复用会话行、换 sid、旧令牌失效
# ---------------------------------------------------------------------------


async def test_same_device_relogin_reuses_session_row(
    client: AsyncClient, db_session, make_captcha
):
    await _set_max_devices(db_session, 2)

    first = await _register(client, make_captcha, device_id="same-browser")
    second = await _login(client, first["user"]["email"], device_id="same-browser")

    rows = await _sessions(db_session, first["user"]["id"])
    assert len(rows) == 1, "同一设备重复登录不应新增会话行"

    sid_first = verify_token(first["access_token"], "access")["sid"]
    sid_second = verify_token(second["access_token"], "access")["sid"]
    assert sid_first != sid_second, "复用行时必须换新 sid"
    assert rows[0].sid == sid_second
    assert rows[0].revoked_at is None

    # 旧令牌（access / refresh）都失效，新令牌可用
    assert (await _me(client, first)).status_code == 401
    assert (await _refresh(client, first)).status_code == 401
    assert (await _me(client, second)).status_code == 200
    assert (await _refresh(client, second)).status_code == 200


# ---------------------------------------------------------------------------
# 4. max=0：不限设备
# ---------------------------------------------------------------------------


async def test_max_zero_never_kicks(
    client: AsyncClient, db_session, make_captcha
):
    await _set_max_devices(db_session, 0)

    a = await _register(client, make_captcha, device_id="device-a")
    b = await _login(client, a["user"]["email"], device_id="device-b")
    c = await _login(client, a["user"]["email"], device_id="device-c")
    d = await _login(client, a["user"]["email"], device_id="device-d")

    for tokens in (a, b, c, d):
        assert (await _me(client, tokens)).status_code == 200

    rows = await _sessions(db_session, a["user"]["id"])
    assert len(rows) == 4
    assert all(row.revoked_at is None for row in rows)


# ---------------------------------------------------------------------------
# 5. logout：当前 access / refresh 立即失效
# ---------------------------------------------------------------------------


async def test_logout_revokes_current_session(
    client: AsyncClient, db_session, make_captcha
):
    tokens = await _register(client, make_captcha, device_id="device-a")

    resp = await client.post("/auth/logout", headers=auth_header(tokens))
    assert resp.status_code == 200

    assert (await _me(client, tokens)).status_code == 401
    assert (await _refresh(client, tokens)).status_code == 401

    rows = await _sessions(db_session, tokens["user"]["id"])
    assert len(rows) == 1
    assert rows[0].revoked_at is not None
    assert rows[0].revoked_reason == "logout"


# ---------------------------------------------------------------------------
# 6. 被踢设备的 refresh 也不能续期
# ---------------------------------------------------------------------------


async def test_kicked_device_refresh_rejected(
    client: AsyncClient, db_session, make_captcha
):
    await _set_max_devices(db_session, 1)

    a = await _register(client, make_captcha, device_id="device-a")
    await _login(client, a["user"]["email"], device_id="device-b")

    resp = await _refresh(client, a)
    assert resp.status_code == 401
    # 被挤下线的设备拿到的不是「登录状态已失效」这种普通文案，而是带
    # code 的结构化 detail（前端据此提示「账号已在其他设备登录」并跳重登）。
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), detail
    assert detail["code"] == "device_limit"
    assert "其他设备登录" in detail["message"]


# ---------------------------------------------------------------------------
# 7. 改密码：当前设备保留，其他设备失效
# ---------------------------------------------------------------------------


async def test_change_password_keeps_current_device_only(
    client: AsyncClient, db_session, make_captcha
):
    await _set_max_devices(db_session, 0)

    a = await _register(client, make_captcha, device_id="device-a")
    b = await _login(client, a["user"]["email"], device_id="device-b")

    change = await client.post(
        "/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=auth_header(a),
    )
    assert change.status_code == 200, change.text
    new_tokens = change.json()

    # 当前设备：旧令牌因版本号失效，响应里的新令牌立即可用且能续期
    assert (await _me(client, a)).status_code == 401
    assert (await _me(client, new_tokens)).status_code == 200
    assert (await _refresh(client, new_tokens)).status_code == 200

    # 其他设备：access 与 refresh 都失效
    assert (await _me(client, b)).status_code == 401
    assert (await _refresh(client, b)).status_code == 401

    rows = await _sessions(db_session, a["user"]["id"])
    by_device = {row.device_id: row for row in rows}
    assert by_device["device-a"].revoked_at is None, "发起改密码的设备不能被踢"
    assert by_device["device-b"].revoked_at is not None
    assert by_device["device-b"].revoked_reason == "password_changed"


# ---------------------------------------------------------------------------
# 8. WebSocket：超限设备以 4002 关闭
#
# 项目现有测试设施没有 starlette TestClient 的 WebSocket 用例，且 chat.py 的
# WS 认证直接使用全局 async_session_factory（不经 conftest 的依赖覆盖），
# 端到端连真实 WS 会打到非测试库。这里改为两层单元测试：
#   - ConnectionManager.close_session 按 sid 关闭全部标签页、不误伤其他设备；
#   - auth._close_revoked_sessions 确实把 WS_CLOSE_DEVICE_LIMIT(4002) 传给管理器。
# 端到端 close 帧的投递（浏览器实际收到 4002）未在此覆盖。
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    def __init__(self) -> None:
        self.closed: list[tuple[int, str]] = []

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def test_ws_close_code_constant_is_4002():
    assert WS_CLOSE_DEVICE_LIMIT == 4002


async def test_close_session_closes_all_tabs_of_the_device_only():
    manager = ConnectionManager()
    tab1, tab2, other_device = _FakeWebSocket(), _FakeWebSocket(), _FakeWebSocket()

    # 同一设备（sid）打开两个标签页，另一台设备一条连接
    await manager.connect(1, tab1, "sid-a")
    await manager.connect(1, tab2, "sid-a")
    await manager.connect(1, other_device, "sid-b")

    closed = await manager.close_session(
        1, "sid-a", WS_CLOSE_DEVICE_LIMIT, "device limit"
    )

    assert closed == 2
    assert tab1.closed == [(4002, "device limit")]
    assert tab2.closed == [(4002, "device limit")]
    assert other_device.closed == []
    assert manager.connections[1] == [("sid-b", other_device)]

    # 未知 sid 不报错、不误伤
    assert await manager.close_session(1, "sid-missing", 4002) == 0
    assert other_device.closed == []


async def test_revoked_sids_are_closed_with_device_limit_code(monkeypatch):
    """登录淘汰返回的 sids 必须以 4002 交给连接管理器。"""
    calls: list[tuple[int, str, int]] = []

    class _RecordingManager:
        async def close_session(self, user_id, sid, code=4002, reason="device limit"):
            calls.append((user_id, sid, code))

    monkeypatch.setattr(
        auth_module, "get_connection_manager", lambda: _RecordingManager()
    )

    # 请求上下文之外没有 post-commit 注册表，run_after_commit 会立即执行
    await auth_module._close_revoked_sessions(42, ["sid-1", "sid-2"])

    assert calls == [(42, "sid-1", 4002), (42, "sid-2", 4002)]


# ---------------------------------------------------------------------------
# 9. 管理员重置密码 / 禁用账号
# ---------------------------------------------------------------------------


async def test_admin_reset_password_invalidates_user_tokens(
    client: AsyncClient, db_session, make_captcha, admin_user: dict
):
    user = await _register(client, make_captcha, device_id="device-a")
    user_id = user["user"]["id"]

    reset = await client.post(
        f"/admin/users/{user_id}/reset-password",
        json={"password": "AdminSetPass9"},
        headers=auth_header(admin_user),
    )
    assert reset.status_code == 200, reset.text

    assert (await _me(client, user)).status_code == 401
    assert (await _refresh(client, user)).status_code == 401

    rows = await _sessions(db_session, user_id)
    assert rows and all(row.revoked_at is not None for row in rows)
    assert {row.revoked_reason for row in rows} == {"admin_reset"}

    # 新密码可以重新登录
    relogin = await client.post(
        "/auth/login",
        json={"email": user["user"]["email"], "password": "AdminSetPass9"},
    )
    assert relogin.status_code == 200


async def test_admin_disable_account_invalidates_user_tokens(
    client: AsyncClient, db_session, make_captcha, admin_user: dict
):
    user = await _register(client, make_captcha, device_id="device-a")
    user_id = user["user"]["id"]

    disabled = await client.post(
        f"/admin/users/{user_id}/status",
        json={"is_active": False},
        headers=auth_header(admin_user),
    )
    assert disabled.status_code == 200, disabled.text

    assert (await _me(client, user)).status_code == 401
    # 禁用账号在 refresh 路径先命中 is_active → 403（既有语义）；会话也已吊销
    assert (await _refresh(client, user)).status_code in (401, 403)

    rows = await _sessions(db_session, user_id)
    assert rows and all(row.revoked_at is not None for row in rows)
    assert {row.revoked_reason for row in rows} == {"disabled"}


# ---------------------------------------------------------------------------
# 边界与回归
# ---------------------------------------------------------------------------


async def test_legacy_token_without_sid_is_rejected(
    client: AsyncClient, make_captcha
):
    """上线前签发的令牌没有 sid：老板要求直接 401，前端自动重登。"""
    tokens = await _register(client, make_captcha, device_id="device-a")
    user_id = tokens["user"]["id"]

    legacy_access = create_access_token(subject=user_id, session_version=0)
    legacy_refresh = create_refresh_token(subject=user_id, session_version=0)

    resp = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {legacy_access}"}
    )
    assert resp.status_code == 401

    resp = await client.post(
        "/auth/refresh", json={"refresh_token": legacy_refresh}
    )
    assert resp.status_code == 401


async def test_lowering_limit_revokes_all_excess_devices(
    client: AsyncClient, db_session, make_captcha
):
    """上限从 0（不限）收紧到 2、已有 4 台在线：第 5 台登录必须一次踢掉 3 台。

    回归点：旧实现边遍历 active 边 remove，会跳过元素，只踢第 1、3 台，
    最终留下 3 台（超过上限）或踢错设备；收紧幅度更大时更明显。
    """
    await _set_max_devices(db_session, 0)

    a = await _register(client, make_captcha, device_id="device-a")
    b = await _login(client, a["user"]["email"], device_id="device-b")
    c = await _login(client, a["user"]["email"], device_id="device-c")
    d = await _login(client, a["user"]["email"], device_id="device-d")

    # 收紧上限后，设备 D 登录触发淘汰
    await _set_max_devices(db_session, 2)
    e = await _login(client, a["user"]["email"], device_id="device-e")

    assert (await _me(client, a)).status_code == 401
    assert (await _me(client, b)).status_code == 401
    assert (await _me(client, c)).status_code == 401
    assert (await _me(client, d)).status_code == 200
    assert (await _me(client, e)).status_code == 200

    rows = await _sessions(db_session, a["user"]["id"])
    active = [row for row in rows if row.revoked_at is None]
    assert [row.device_id for row in active] == ["device-d", "device-e"]
    revoked = [row for row in rows if row.revoked_at is not None]
    assert {row.device_id for row in revoked} == {"device-a", "device-b", "device-c"}
    assert {row.revoked_reason for row in revoked} == {"device_limit"}


async def test_revoke_others_without_keep_revokes_every_session(
    client: AsyncClient, db_session, make_captcha
):
    """keep_sid 为空时必须吊销全部会话（不能因 ``sid != ''`` 依赖空串假设）。"""
    a = await _register(client, make_captcha, device_id="device-a")
    await _login(client, a["user"]["email"], device_id="device-b")
    user_id = a["user"]["id"]

    revoked = await user_session_service.revoke_others(
        db_session, user_id, keep_sid=None, reason="password_changed"
    )
    await db_session.commit()

    assert len(revoked) == 2
    rows = await _sessions(db_session, user_id)
    assert all(row.revoked_at is not None for row in rows)
    assert {row.revoked_reason for row in rows} == {"password_changed"}
