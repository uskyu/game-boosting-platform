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

from app.core.config import settings
from app.models.user import User
from app.models.user_session import UserSession
from app.services.site_setting_service import get_or_create_site_setting

# 被踢设备的 WebSocket 关闭码：与 4001（账号级会话失效，客户端自行验证）
# 区分开，客户端收到 4002 直接登出，不再重连。
WS_CLOSE_DEVICE_LIMIT = 4002


def _now() -> datetime:
    """数据库存的是 naive UTC，统一用这个口径。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def get_max_devices(db) -> int:
    setting = await get_or_create_site_setting(db)
    try:
        return max(int(setting.max_online_devices or 0), 0)
    except (TypeError, ValueError):
        return 0


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

    now = _now()
    expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
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
    max_devices = await get_max_devices(db)
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
