"""指定派单候选人业务逻辑。

- 仅实际可发单的登录用户可调用（管理员始终可发单）；
- 候选范围 = 活跃、非管理员、非当前用户、未被当前发布人拉黑的账号；
  ``can_accept=False`` 的活跃账号仍保留，由前端标记「已停接」并禁止选择；
- 对外不返回登录邮箱；
- 「最近指定」只看当前发布人过去 90 天内带 designated_booster_id 的订单。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import escape_like
from app.models.order import Order
from app.models.publisher_blacklist import PublisherBlacklist
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from app.schemas.designation import DesignationCandidate
from app.services.publish_permission_service import (
    can_publish_effective,
    publish_forbidden_detail,
)
from app.services.site_setting_service import get_or_create_site_setting

# 最近指定的时间窗口与列表上限（前端会缓存最近列表）
RECENT_DESIGNATION_DAYS = 90
RECENT_DESIGNATION_LIMIT = 6
CANDIDATE_SEARCH_LIMIT = 10


def _utcnow() -> datetime:
    """数据库统一存 naive UTC（项目约定）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def ensure_can_designate(db: AsyncSession, user: User) -> None:
    """校验当前用户实际可发单；与发单接口共用同一权限口径与 403 文案。"""
    setting = await get_or_create_site_setting(db)
    required = bool(setting.publish_permission_required)
    if can_publish_effective(user, required=required):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=publish_forbidden_detail(user, required=required),
    )


def _candidate_filters() -> tuple:
    """候选账号的公共过滤条件（搜索与最近指定共用）。

    自己/黑名单不再静默排除：命中时仍返回，由 ``is_self``/``is_blacklisted``
    标记并由前端禁用选择，避免「明明有这个人却提示没找到」。
    """
    return (
        User.is_active.is_(True),
        User.role != UserRole.ADMIN,
    )


def _blacklisted_expr(viewer_id: int):
    """当前发布人是否拉黑了该候选（用于候选人标记，不做静默过滤）。"""
    return exists(
        select(PublisherBlacklist.id).where(
            PublisherBlacklist.publisher_id == viewer_id,
            PublisherBlacklist.blocked_user_id == User.id,
        )
    )


def _designation_counts_subquery(viewer_id: int, cutoff: datetime):
    """当前发布人在窗口内对每个打手的指定次数与最近指定时间。"""
    return (
        select(
            Order.designated_booster_id.label("user_id"),
            func.count(Order.id).label("selected_times"),
            func.max(Order.created_at).label("last_selected_at"),
        )
        .where(
            Order.user_id == viewer_id,
            Order.designated_booster_id.is_not(None),
            Order.created_at >= cutoff,
        )
        .group_by(Order.designated_booster_id)
        .subquery()
    )


def _row_to_candidate(row, viewer_id: int) -> DesignationCandidate:
    balance = getattr(row, "deposit_balance", None)
    return DesignationCandidate(
        id=int(row.user_id),
        username=row.username,
        avatar_url=row.avatar_url,
        deposit_balance=balance if balance is not None else Decimal("0.00"),
        can_accept=bool(row.can_accept),
        selected_times=int(row.selected_times or 0),
        is_self=int(row.user_id) == int(viewer_id),
        is_blacklisted=bool(getattr(row, "is_blacklisted", False)),
    )


async def list_recent_candidates(
    db: AsyncSession,
    viewer: User,
) -> list[DesignationCandidate]:
    """最近 90 天指定过的打手：次数优先、最近指定时间其次，最多 6 人。"""
    cutoff = _utcnow() - timedelta(days=RECENT_DESIGNATION_DAYS)
    counts = _designation_counts_subquery(viewer.id, cutoff)

    result = await db.execute(
        select(
            User.id.label("user_id"),
            User.username.label("username"),
            User.avatar_url.label("avatar_url"),
            func.coalesce(Wallet.deposit_balance, Decimal("0.00")).label(
                "deposit_balance"
            ),
            User.can_accept.label("can_accept"),
            counts.c.selected_times.label("selected_times"),
            _blacklisted_expr(viewer.id).label("is_blacklisted"),
        )
        .select_from(User)
        .join(counts, counts.c.user_id == User.id)
        .outerjoin(Wallet, Wallet.user_id == User.id)
        .where(*_candidate_filters())
        .order_by(
            counts.c.selected_times.desc(),
            counts.c.last_selected_at.desc(),
            User.username.asc(),
            User.id.asc(),
        )
        .limit(RECENT_DESIGNATION_LIMIT)
    )
    return [_row_to_candidate(row, viewer.id) for row in result.all()]


async def search_candidates(
    db: AsyncSession,
    viewer: User,
    keyword: str,
) -> list[DesignationCandidate]:
    """按昵称/登录账号模糊搜索候选人，最多 10 人。

    登录账号只用于匹配，绝不出现在响应；精确命中优先，其次前缀命中，
    同档按昵称排序。
    """
    cutoff = _utcnow() - timedelta(days=RECENT_DESIGNATION_DAYS)
    counts = _designation_counts_subquery(viewer.id, cutoff)

    escaped = escape_like(keyword)
    contains_pattern = f"%{escaped}%"
    prefix_pattern = f"{escaped}%"
    exact_match = or_(
        User.username == keyword,
        User.email == keyword,
    )
    prefix_match = or_(
        User.username.ilike(prefix_pattern),
        User.email.ilike(prefix_pattern),
    )
    match_rank = case(
        (exact_match, 0),
        (prefix_match, 1),
        else_=2,
    )

    result = await db.execute(
        select(
            User.id.label("user_id"),
            User.username.label("username"),
            User.avatar_url.label("avatar_url"),
            func.coalesce(Wallet.deposit_balance, Decimal("0.00")).label(
                "deposit_balance"
            ),
            User.can_accept.label("can_accept"),
            func.coalesce(counts.c.selected_times, 0).label("selected_times"),
            _blacklisted_expr(viewer.id).label("is_blacklisted"),
        )
        .select_from(User)
        .outerjoin(counts, counts.c.user_id == User.id)
        .outerjoin(Wallet, Wallet.user_id == User.id)
        .where(
            or_(
                User.username.ilike(contains_pattern),
                User.email.ilike(contains_pattern),
            ),
            *_candidate_filters(),
        )
        .order_by(match_rank.asc(), User.username.asc(), User.id.asc())
        .limit(CANDIDATE_SEARCH_LIMIT)
    )
    return [_row_to_candidate(row, viewer.id) for row in result.all()]
