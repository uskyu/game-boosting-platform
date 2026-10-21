"""改密码后旧登录必须立即失效（会话版本号机制）。

老板实测问题（2026-10-21）：用户改完密码，**改密码之前登录的那一端还在正常
使用**，指望改密码把别人踢下线完全无效。

根因不是密码哈希，而是 JWT 天然无状态：``users.hashed_password`` 变了只影响
「下一次用密码换令牌」，对已经发出去的 access/refresh 令牌没有任何约束力；
而 refresh 每次都能换一对新的，等于旧登录可以无限续期。改成 ``lazy`` 式的
"看起来会失效"都不成立，必须引入服务端可比对的会话版本号。

本文件锁死这些行为：
1. 改密码 → 旧 access 401、旧 refresh 401、新令牌可用；
2. 改密码的人自己不掉线（响应直接带回新令牌）；
3. 同一账号的其他会话一起失效（多设备）；
4. 管理员重置密码 → 该用户旧令牌 401（盗号场景）；
5. **其他用户完全不受影响**（防止改错范围导致全站强制重登）；
6. 改动前签发的老令牌（没有 ver 声明）在版本号为 0 时仍然可用，
   即部署本次改动不会把任何已登录用户踢下线。
"""

import pytest
from httpx import AsyncClient

from app.core.security import (
    create_access_token,
    create_refresh_token,
    token_session_version,
    verify_token,
)
from tests.conftest import auth_header

OLD_PASSWORD = "TestPass123"   # conftest registered_user 的初始密码
NEW_PASSWORD = "BrandNewPass9"


async def _register(client: AsyncClient, make_captcha, email: str, username: str) -> dict:
    resp = await client.post("/auth/register", json={
        "email": email,
        "username": username,
        "password": OLD_PASSWORD,
        **make_captcha(),
    })
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _change_password(client: AsyncClient, tokens: dict, new_password: str = NEW_PASSWORD):
    return await client.post(
        "/auth/change-password",
        json={
            "current_password": OLD_PASSWORD if new_password == NEW_PASSWORD else NEW_PASSWORD,
            "new_password": new_password,
        },
        headers=auth_header(tokens),
    )


# =============================================================================
# 核心行为：改密码后旧登录失效
# =============================================================================

async def test_old_access_token_rejected_after_password_change(
    client: AsyncClient, registered_user: dict
):
    """改密码后，改密码之前签发的 access 令牌必须立刻 401。"""
    # 改之前：令牌可用
    resp = await client.get("/auth/me", headers=auth_header(registered_user))
    assert resp.status_code == 200

    change = await _change_password(client, registered_user)
    assert change.status_code == 200, change.text

    resp = await client.get("/auth/me", headers=auth_header(registered_user))
    assert resp.status_code == 401
    assert "登录状态已失效" in resp.json()["detail"]


async def test_old_refresh_token_rejected_after_password_change(
    client: AsyncClient, registered_user: dict
):
    """旧 refresh 也不能再换出新 access——否则密码改了等于没改。"""
    change = await _change_password(client, registered_user)
    assert change.status_code == 200, change.text

    resp = await client.post("/auth/refresh", json={
        "refresh_token": registered_user["refresh_token"],
    })
    assert resp.status_code == 401
    assert "登录状态已失效" in resp.json()["detail"]


async def test_password_change_response_returns_usable_new_tokens(
    client: AsyncClient, registered_user: dict
):
    """操作者本人不掉线：响应带回新令牌，且新令牌立即可用。"""
    change = await _change_password(client, registered_user)
    assert change.status_code == 200, change.text
    data = change.json()

    assert data["access_token"] != registered_user["access_token"]
    assert data["refresh_token"] != registered_user["refresh_token"]

    new_header = {"Authorization": f"Bearer {data['access_token']}"}
    resp = await client.get("/auth/me", headers=new_header)
    assert resp.status_code == 200
    assert resp.json()["id"] == registered_user["user"]["id"]

    # 新 refresh 也要能正常续期，否则用户 2 小时后会被莫名其妙踢下线
    refreshed = await client.post("/auth/refresh", json={
        "refresh_token": data["refresh_token"],
    })
    assert refreshed.status_code == 200


async def test_new_password_works_and_old_password_rejected(
    client: AsyncClient, registered_user: dict
):
    """改完之后：新密码能登录，旧密码登不上。"""
    email = registered_user["user"]["email"]
    change = await _change_password(client, registered_user)
    assert change.status_code == 200, change.text

    old_login = await client.post("/auth/login", json={
        "email": email,
        "password": OLD_PASSWORD,
    })
    assert old_login.status_code == 401

    new_login = await client.post("/auth/login", json={
        "email": email,
        "password": NEW_PASSWORD,
    })
    assert new_login.status_code == 200


# =============================================================================
# 多设备：同一账号的另一个会话一起失效
# =============================================================================

async def test_other_device_session_invalidated(
    client: AsyncClient, registered_user: dict
):
    """线上真实场景：在设备 A 改密码，设备 B 的登录必须立刻失效。

    这里用同一个账号再登录一次模拟设备 B，得到一份独立令牌。
    """
    email = registered_user["user"]["email"]
    device_b = await client.post("/auth/login", json={
        "email": email,
        "password": OLD_PASSWORD,
    })
    assert device_b.status_code == 200
    device_b_tokens = device_b.json()

    # 两个设备都还活着
    assert (await client.get("/auth/me", headers=auth_header(device_b_tokens))).status_code == 200

    # 设备 A 改密码
    change = await _change_password(client, registered_user)
    assert change.status_code == 200, change.text

    # 设备 B 立刻失效
    resp = await client.get("/auth/me", headers=auth_header(device_b_tokens))
    assert resp.status_code == 401
    refresh_b = await client.post("/auth/refresh", json={
        "refresh_token": device_b_tokens["refresh_token"],
    })
    assert refresh_b.status_code == 401


async def test_other_users_are_unaffected(
    client: AsyncClient, registered_user: dict, make_captcha
):
    """改密码不能误伤其他用户（防止版本比对写错导致全站重新登录）。"""
    bystander = await _register(client, make_captcha, "bystander@example.com", "Bystander")

    change = await _change_password(client, registered_user)
    assert change.status_code == 200, change.text

    resp = await client.get("/auth/me", headers=auth_header(bystander))
    assert resp.status_code == 200
    assert resp.json()["email"] == "bystander@example.com"


# =============================================================================
# 管理员重置密码（盗号场景）
# =============================================================================

async def test_admin_password_reset_invalidates_user_sessions(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    """管理员重置某个用户的密码后，该用户手里的旧令牌必须失效。

    这是比自助改密码更关键的一条：账号被盗时管理员重置密码，如果攻击者
    手里的令牌还能用，重置就白做了。
    """
    user_id = registered_user["user"]["id"]

    reset = await client.post(
        f"/admin/users/{user_id}/reset-password",
        json={"password": "AdminSetPass9"},
        headers=auth_header(admin_user),
    )
    assert reset.status_code == 200, reset.text
    assert "退出登录" in reset.json()["message"]

    resp = await client.get("/auth/me", headers=auth_header(registered_user))
    assert resp.status_code == 401
    refresh = await client.post("/auth/refresh", json={
        "refresh_token": registered_user["refresh_token"],
    })
    assert refresh.status_code == 401

    # 管理员自己不受影响
    admin_me = await client.get("/auth/me", headers=auth_header(admin_user))
    assert admin_me.status_code == 200

    # 被告知的新密码可用
    login = await client.post("/auth/login", json={
        "email": registered_user["user"]["email"],
        "password": "AdminSetPass9",
    })
    assert login.status_code == 200


async def test_admin_reset_password_only_affects_target_user(
    client: AsyncClient, registered_user: dict, admin_user: dict, make_captcha
):
    """管理员重置 A 的密码，不能把 B 也踢下线。"""
    other = await _register(client, make_captcha, "other-target@example.com", "OtherTarget")

    reset = await client.post(
        f"/admin/users/{registered_user['user']['id']}/reset-password",
        json={"password": "AdminSetPass9"},
        headers=auth_header(admin_user),
    )
    assert reset.status_code == 200

    assert (await client.get("/auth/me", headers=auth_header(other))).status_code == 200


# =============================================================================
# 兼容性：部署本次改动不能踢掉现有在线用户
# =============================================================================

def test_legacy_token_without_ver_treated_as_zero():
    """改动前签发的令牌没有 ver 声明，按 0 处理。

    现有用户的 token_version 默认也是 0，所以上线这个改动**不会**把任何
    已登录用户踢下线——这一条是部署安全的关键前提。
    """
    from jose import jwt

    from app.core.config import settings

    legacy_payload = {
        "sub": "1",
        "type": "access",
        "iat": 1_700_000_000,
        "exp": 4_000_000_000,
    }
    legacy_token = jwt.encode(legacy_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    payload = verify_token(legacy_token, token_type="access")
    assert payload is not None
    assert "ver" not in payload
    assert token_session_version(payload) == 0


def test_new_tokens_carry_session_version():
    """新签发的令牌带上 ver，且能被读回。"""
    access = create_access_token(subject=7, session_version=3)
    payload = verify_token(access, token_type="access")
    assert payload is not None
    assert payload["ver"] == 3
    assert token_session_version(payload) == 3

    refresh = create_refresh_token(subject=7, session_version=5)
    payload = verify_token(refresh, token_type="refresh")
    assert payload is not None
    assert token_session_version(payload) == 5


def test_garbage_ver_claim_is_rejected():
    """ver 被塞成非数字时按最低版本处理（等同失效），不抛异常。"""
    assert token_session_version({"ver": "abc"}) == -1
    assert token_session_version({"ver": None}) == 0
    assert token_session_version({}) == 0


async def test_token_version_increments_on_each_change(
    client: AsyncClient, registered_user: dict, db_session
):
    """每改一次密码，版本号 +1（不是置位，避免连续两次改动后新令牌又变有效）。"""
    from sqlalchemy import select

    from app.models.user import User

    user_id = registered_user["user"]["id"]

    async def current_version() -> int:
        await db_session.commit()  # 结束旧事务，读到接口刚提交的值
        result = await db_session.execute(select(User.token_version).where(User.id == user_id))
        return result.scalar_one()

    assert await current_version() == 0

    first = await _change_password(client, registered_user)
    assert first.status_code == 200
    assert await current_version() == 1

    # 用刚拿到的新令牌再改一次
    second = await client.post(
        "/auth/change-password",
        json={"current_password": NEW_PASSWORD, "new_password": "ThirdPass77"},
        headers={"Authorization": f"Bearer {first.json()['access_token']}"},
    )
    assert second.status_code == 200, second.text
    assert await current_version() == 2

    # 第一次改密码拿到的令牌此时也失效了（版本已经推进到 2）
    stale = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {first.json()['access_token']}"},
    )
    assert stale.status_code == 401


# =============================================================================
# 反向守卫：不能因为版本机制把正常流程改坏
# =============================================================================

async def test_wrong_current_password_does_not_invalidate_sessions(
    client: AsyncClient, registered_user: dict
):
    """当前密码输错时不能动版本号——否则任何人试错就能把别人踢下线。"""
    resp = await client.post(
        "/auth/change-password",
        json={"current_password": "WrongPass123", "new_password": NEW_PASSWORD},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 400
    assert "当前密码错误" in resp.json()["detail"]

    # 令牌仍然可用
    assert (await client.get("/auth/me", headers=auth_header(registered_user))).status_code == 200
    # 旧密码仍然能登录
    login = await client.post("/auth/login", json={
        "email": registered_user["user"]["email"],
        "password": OLD_PASSWORD,
    })
    assert login.status_code == 200


async def test_unauthenticated_change_password_still_rejected(client: AsyncClient):
    """没登录不能改密码（版本机制不能顺带把鉴权绕过去）。"""
    resp = await client.post("/auth/change-password", json={
        "current_password": "x",
        "new_password": NEW_PASSWORD,
    })
    assert resp.status_code == 401


async def test_disabled_account_still_blocked_after_version_check(
    client: AsyncClient, registered_user: dict, db_session
):
    """禁用账号返回 403（不是被版本比对覆盖成 401），语义不能被改坏。"""
    from sqlalchemy import select

    from app.models.user import User

    await db_session.execute(
        select(User).where(User.id == registered_user["user"]["id"])
    )
    user = (await db_session.execute(
        select(User).where(User.id == registered_user["user"]["id"])
    )).scalar_one()
    user.is_active = False
    await db_session.commit()

    resp = await client.get("/auth/me", headers=auth_header(registered_user))
    assert resp.status_code == 403
    assert "已被禁用" in resp.json()["detail"]


@pytest.mark.parametrize("new_password", [
    "short1A",       # 太短
    "alldigits123",  # 无大写
    "NoDigitsHere",  # 无数字
])
async def test_new_password_rules_unchanged(
    client: AsyncClient, registered_user: dict, new_password: str
):
    """密码强度规则不能被本次改动放松；校验失败也不能动版本号。"""
    resp = await client.post(
        "/auth/change-password",
        json={"current_password": OLD_PASSWORD, "new_password": new_password},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 422
    # 失败后原令牌仍有效
    assert (await client.get("/auth/me", headers=auth_header(registered_user))).status_code == 200
