"""后台可调「登录保持天数」（老板口中的 Cookie 登录时间）。

锁定行为：
1. 默认 7 天，且公开设置接口可读；
2. 管理员改成 30 天后，**新登录**的 refresh token exp 与设备会话
   expires_at 都约等于 30 天；
3. 越界值（<1 / >365）在 schema 层被拒绝；
4. 刷新令牌续签、改密码返回的新 refresh 都使用当前设置；
5. 未传字段保持原值，不会被全量保存表单重置。
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.models.site_setting import SiteSetting
from app.models.user_session import UserSession
from app.services.site_setting_service import normalize_login_session_days
from tests.conftest import auth_header

REGISTER_PASSWORD = "TestPass123"


def _refresh_expires_in_days(token: str) -> float:
    payload = jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],
    )
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    delta = exp - datetime.now(timezone.utc)
    return delta.total_seconds() / 86400


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _set_days(client: AsyncClient, admin_user: dict, days: int) -> dict:
    response = await client.put(
        "/admin/site/settings",
        json={"site_name": "游戏服务平台", "login_session_days": days},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text
    assert response.json()["login_session_days"] == days
    return response.json()


# ---------------------------------------------------------------------------
# 默认值与归一化
# ---------------------------------------------------------------------------


async def test_login_session_days_defaults_to_seven_and_is_public(client: AsyncClient):
    data = (await client.get("/site/settings")).json()
    assert data["login_session_days"] == 7


def test_normalize_login_session_days_falls_back_and_clamps():
    assert normalize_login_session_days(None) == min(
        max(int(settings.REFRESH_TOKEN_EXPIRE_DAYS), 1), 365
    )
    assert normalize_login_session_days("bad") == min(
        max(int(settings.REFRESH_TOKEN_EXPIRE_DAYS), 1), 365
    )
    assert normalize_login_session_days(0) == 1
    assert normalize_login_session_days(999) == 365


# ---------------------------------------------------------------------------
# 保存与生效
# ---------------------------------------------------------------------------


async def test_admin_setting_30_applies_to_new_login(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session,
):
    await _set_days(client, admin_user, 30)

    # 已注册用户在设置生效后重新登录：新的 refresh 与设备会话都按 30 天
    login = await client.post(
        "/auth/login",
        json={
            "email": registered_user["user"]["email"],
            "password": REGISTER_PASSWORD,
            "device_id": "days-30-device",
        },
    )
    assert login.status_code == 200, login.text
    tokens = login.json()
    assert _refresh_expires_in_days(tokens["refresh_token"]) == pytest.approx(
        30, abs=0.01
    )

    await db_session.commit()  # 结束旧事务，读取接口刚提交的会话行
    row = (
        await db_session.execute(
            select(UserSession).where(
                UserSession.user_id == registered_user["user"]["id"],
                UserSession.device_id == "days-30-device",
            )
        )
    ).scalar_one()
    remaining_days = (
        row.expires_at - _naive_utc_now()
    ).total_seconds() / 86400
    assert remaining_days == pytest.approx(30, abs=0.01)


async def test_omitting_field_keeps_existing_value(
    client: AsyncClient, admin_user: dict, db_session
):
    await _set_days(client, admin_user, 30)

    response = await client.put(
        "/admin/site/settings",
        json={"site_name": "平台改名"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["login_session_days"] == 30

    await db_session.commit()
    stored = (
        await db_session.execute(
            select(SiteSetting.login_session_days).where(SiteSetting.id == 1)
        )
    ).scalar_one()
    assert stored == 30


@pytest.mark.parametrize("days", [0, 366, -1, 1000])
async def test_login_session_days_out_of_range_rejected(
    client: AsyncClient, admin_user: dict, days: int
):
    response = await client.put(
        "/admin/site/settings",
        json={"site_name": "游戏服务平台", "login_session_days": days},
        headers=auth_header(admin_user),
    )
    # 项目统一由 RequestValidationError handler 返回 422；兼容网关改写 400。
    assert response.status_code in (400, 422), response.text
    assert (await client.get("/site/settings")).json()["login_session_days"] == 7


# ---------------------------------------------------------------------------
# 续签与改密码也使用当前设置
# ---------------------------------------------------------------------------


async def test_refresh_issues_new_refresh_with_current_setting(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _set_days(client, admin_user, 30)

    response = await client.post(
        "/auth/refresh",
        json={"refresh_token": registered_user["refresh_token"]},
    )
    assert response.status_code == 200, response.text
    assert _refresh_expires_in_days(response.json()["refresh_token"]) == pytest.approx(
        30, abs=0.01
    )


async def test_change_password_returns_refresh_with_current_setting(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _set_days(client, admin_user, 30)

    response = await client.post(
        "/auth/change-password",
        json={
            "current_password": REGISTER_PASSWORD,
            "new_password": "BrandNewPass9",
        },
        headers=auth_header(registered_user),
    )
    assert response.status_code == 200, response.text
    assert _refresh_expires_in_days(response.json()["refresh_token"]) == pytest.approx(
        30, abs=0.01
    )
