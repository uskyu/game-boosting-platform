"""登录设备会话：创建、校验、吊销。

每个设备（浏览器实例）一条 ``user_sessions`` 记录，``sid`` 写进 JWT。
超过后台配置的 ``max_online_devices`` 时吊销最早登录的会话；WS 侧由
``connection_manager.close_session`` 主动断开。

并发：登录时对用户行加 ``FOR UPDATE``，串行化同一账号的"计数 + 淘汰"，
避免两台设备同时登录都通过上限检查。
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select

from app.models.user import User
from app.models.user_session import UserSession
from app.services.site_setting_service import (
    get_or_create_site_setting,
    normalize_login_session_days,
)

# 被踢设备的 WebSocket 关闭码：与 4001（账号级会话失效，客户端自行验证）
# 区分开，客户端收到 4002 直接登出，不再重连。
WS_CLOSE_DEVICE_LIMIT = 4002
# 账号级会话失效（改密码/管理员重置/登出）：客户端先验证会话，再决定是否重连。
WS_CLOSE_SESSION_INVALIDATED = 4001

# 401 结构化 detail：revoked_reason 属于内部字段，只映射为固定的
# code + message 下发，避免把后台处置细节原样暴露给客户端。
_SESSION_REASON_DETAILS: dict[str, tuple[str, str]] = {
    "password_changed": ("password_changed", "密码已修改，请重新登录"),
    "admin_reset": ("admin_reset", "账号密码已被管理员重置，请重新登录"),
    "disabled": ("disabled", "账号已被停用，请联系管理员"),
    "device_limit": ("device_limit", "账号已在其他设备登录，当前设备已下线"),
}


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def session_invalidated_detail() -> dict[str, str]:
    return _detail("session_invalidated", "登录状态已失效，请重新登录")


def session_expired_detail() -> dict[str, str]:
    return _detail("session_expired", "登录已过期，请重新登录")


def password_changed_detail() -> dict[str, str]:
    return _detail("password_changed", "密码已修改，请重新登录")


def session_invalidation_detail(
    revoked_reason: str | None,
    expires_at: datetime | None,
    *,
    revoked_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """把会话记录的吊销原因/到期时间翻译成统一的 401 detail。

    - 有已知吊销原因（改密码/管理员重置/停用/设备超限）→ 对应 code；
    - 未吊销且 expires_at 已过 → ``session_expired``；
    - 已登出、无记录、原因未知或其他情况 → ``session_invalidated``。
    """
    if revoked_reason in _SESSION_REASON_DETAILS:
        code, message = _SESSION_REASON_DETAILS[revoked_reason]
        return _detail(code, message)
    if revoked_at is None and revoked_reason is None:
        current = now or _now()
        if expires_at is not None and expires_at <= current:
            return session_expired_detail()
    return session_invalidated_detail()


async def get_session_invalidation_detail(
    db, sid: str | None, user_id: int
) -> dict[str, str] | None:
    """按 sid 查询会话记录并返回失效 detail；没有记录时返回 None。

    deps 与 user_service 共用这一份查询/映射，避免两处各写一套原因判断。
    """
    if not sid:
        return None
    result = await db.execute(
        select(
            UserSession.revoked_at,
            UserSession.revoked_reason,
            UserSession.expires_at,
        ).where(
            UserSession.sid == sid,
            UserSession.user_id == user_id,
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    return session_invalidation_detail(
        revoked_reason=row.revoked_reason,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


def _now() -> datetime:
    """数据库存的是 naive UTC，统一用这个口径。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _max_devices_from_setting(setting) -> int:
    try:
        return max(int(setting.max_online_devices or 0), 0)
    except (TypeError, ValueError):
        return 0


async def get_max_devices(db) -> int:
    setting = await get_or_create_site_setting(db)
    return _max_devices_from_setting(setting)


async def start_session(
    db,
    user: User,
    *,
    device_id: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[UserSession, list[str]]:
    """登录/注册时创建（或复用）设备会话，并淘汰超限的最早设备。

    Returns:
        ``(session, revoked_sids)``；``revoked_sids`` 需要在事务提交后关闭
        对应的 WebSocket。
    """
    # 同一账号的并发登录串行化：先锁用户行再计数、淘汰、建会话。
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())

    # 一次读取站点设置：登录保持天数与同时在线设备上限来自同一行，
    # 不要为了两个列各查一遍。
    setting = await get_or_create_site_setting(db)
    login_days = normalize_login_session_days(setting.login_session_days)
    max_devices = _max_devices_from_setting(setting)

    now = _now()
    expires_at = now + timedelta(days=login_days)
    sid = uuid.uuid4().hex
    agent = (user_agent or "")[:255] or None
    device = (device_id or "").strip()[:64] or None

    session: UserSession | None = None
    if device:
        # ``FOR UPDATE`` 不仅锁行，更关键的是「当前读」：MySQL 默认
        # REPEATABLE READ，登录接口前面的用户查询已经建立了事务快照，
        # 拿到用户行锁之后普通 SELECT 仍看不到刚并发提交的会话行，会导致
        # 同一设备重复建行/淘汰漏算。锁定读始终读最新已提交版本。
        result = await db.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.device_id == device,
                UserSession.revoked_at.is_(None),
            )
            .order_by(UserSession.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        session = result.scalar_one_or_none()

    if session is not None:
        # 同一浏览器重复登录：复用行、换新 sid（旧令牌立即失效），
        # 登录时间刷新为最新，不额外占用设备名额。
        session.sid = sid
        session.created_at = now
        session.last_seen_at = now
        session.expires_at = expires_at
        session.user_agent = agent
        session.ip_address = ip_address
    else:
        session = UserSession(
            user_id=user.id,
            sid=sid,
            device_id=device,
            user_agent=agent,
            ip_address=ip_address,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
        )
        db.add(session)

    await db.flush()

    revoked_sids: list[str] = []
    if max_devices > 0:
        result = await db.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.created_at.asc(), UserSession.id.asc())
            # 当前读：与上面的复用查询同理，必须看到并发登录刚提交的会话，
            # 否则两台设备同时登录会各自按旧快照计数、都通过上限检查。
            .with_for_update()
        )
        active = list(result.scalars().all())
        # 需要淘汰的条数按「当前活跃会话数 - 上限」计算。不要在迭代列表的
        # 同时 remove：那样会跳过元素（限流从 5 改成 2 这类需要一次淘汰
        # 多台的场景会漏踢，最终仍超过上限）。
        excess = len(active) - max_devices
        for victim in active:
            if excess <= 0:
                break
            # 当前正在登录的这台永不淘汰（它是本次请求的主体）。
            if victim.id == session.id:
                continue
            victim.revoked_at = now
            victim.revoked_reason = "device_limit"
            revoked_sids.append(victim.sid)
            excess -= 1

    return session, revoked_sids


async def get_valid_session(db, sid: str | None, user_id: int) -> UserSession | None:
    """取仍然有效的会话（未吊销、未过期）。"""
    if not sid:
        return None
    result = await db.execute(
        select(UserSession).where(
            UserSession.sid == sid,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > _now(),
        )
    )
    return result.scalar_one_or_none()


async def revoke_session(db, sid: str | None, reason: str = "logout") -> bool:
    if not sid:
        return False
    session = await db.execute(
        select(UserSession).where(
            UserSession.sid == sid,
            UserSession.revoked_at.is_(None),
        )
    )
    row = session.scalar_one_or_none()
    if row is None:
        return False
    row.revoked_at = _now()
    row.revoked_reason = reason[:32]
    await db.flush()
    return True


async def revoke_others(
    db, user_id: int, *, keep_sid: str | None, reason: str = "password_changed"
) -> list[str]:
    """吊销该用户的全部会话，保留 ``keep_sid``（改密码时保留当前设备）。

    ``keep_sid`` 为空时吊销全部。显式分支而不是依赖 ``sid != ""``：虽然
    ``sid`` 由 ``uuid4().hex`` 生成、不可能为空串，但把语义写清楚可以避免
    以后有人把 sid 换成允许空值的方案时悄悄少踢一条。
    """
    conditions = [
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
    ]
    if keep_sid:
        conditions.append(UserSession.sid != keep_sid)
    result = await db.execute(select(UserSession).where(*conditions))
    now = _now()
    revoked: list[str] = []
    for row in result.scalars().all():
        row.revoked_at = now
        row.revoked_reason = reason[:32]
        revoked.append(row.sid)
    await db.flush()
    return revoked


async def revoke_all(db, user_id: int, reason: str = "session_invalidated") -> list[str]:
    result = await db.execute(
        select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
    )
    now = _now()
    revoked: list[str] = []
    for row in result.scalars().all():
        row.revoked_at = now
        row.revoked_reason = reason[:32]
        revoked.append(row.sid)
    await db.flush()
    return revoked


async def require_valid_session(db, sid: str | None, user_id: int) -> UserSession:
    """鉴权用：会话缺失/已吊销/已过期一律 401（老登录自动掉线）。"""
    session = await get_valid_session(db, sid, user_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录状态已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return session
