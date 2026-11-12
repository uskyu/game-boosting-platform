"""发单人黑名单业务逻辑。

设计口径（2026-11-11 冻结）：
- 平表，只做拉黑/解除，不做等级、处罚阶梯或订单级规则；
- 只影响「被拉黑用户接该发单人的订单」，大厅/详情仍可见；
- 拉黑对象只能来自精确查找（用户名/登录邮箱）或订单接单名单，不提供
  全站用户列表/模糊搜索能力；
- 单发单人最多 500 条，重复拉黑幂等。
"""

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import escape_like
from app.models.publisher_blacklist import PublisherBlacklist
from app.models.user import User, UserRole
from app.schemas.blacklist import PublisherBlacklistItem, PublisherBlacklistLookupResponse
from app.services.publish_permission_service import can_publish_effective
from app.services.site_setting_service import get_or_create_site_setting

# 单发单人上限（服务层校验）
BLACKLIST_LIMIT_PER_PUBLISHER = 500

# 对外固定文案：接口 403 / 列表标记 / 详情提示必须逐字一致
ACCEPT_BLOCKED_DETAIL = "你已被该发布人拉黑，无法接此单"
PUBLISHER_ONLY_DETAIL = "仅发单用户可使用黑名单"
TARGET_NOT_FOUND_DETAIL = "未找到该用户"
TARGET_FORBIDDEN_DETAIL = "该用户不可拉黑"
LIMIT_EXCEEDED_DETAIL = "黑名单已达上限（最多 500 人）"


async def ensure_can_manage_blacklist(db: AsyncSession, user: User) -> None:
    """黑名单只允许管理员或当前实际可发单的用户使用。

    全站总开关关闭时，未封禁的非管理员无需已开通发单权限也可使用；
    被封禁发单的用户始终不能用（与发单权限口径一致）。
    """
    setting = await get_or_create_site_setting(db)
    if can_publish_effective(
        user, required=bool(setting.publish_permission_required)
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=PUBLISHER_ONLY_DETAIL,
    )


def _clean_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    clean = reason.strip()
    return clean or None


def _to_item(
    entry: PublisherBlacklist,
    *,
    username: str,
    avatar_url: str | None,
) -> PublisherBlacklistItem:
    return PublisherBlacklistItem(
        id=entry.id,
        blocked_user_id=entry.blocked_user_id,
        username=username,
        avatar_url=avatar_url,
        reason=entry.reason,
        created_at=entry.created_at,
    )


async def list_mine(
    db: AsyncSession,
    publisher: User,
    *,
    q: str | None,
    page: int,
    page_size: int,
) -> tuple[list[PublisherBlacklistItem], int]:
    """当前用户自己的黑名单分页；q 只对已拉黑列表做用户名过滤。"""
    filters = [PublisherBlacklist.publisher_id == publisher.id]
    keyword = (q or "").strip()
    if keyword:
        # 只在「我拉黑的人」里按用户名过滤，绝不扩展成全站搜索
        filters.append(User.username.ilike(f"%{escape_like(keyword)}%"))

    total_result = await db.execute(
        select(func.count(PublisherBlacklist.id))
        .select_from(PublisherBlacklist)
        .join(User, User.id == PublisherBlacklist.blocked_user_id)
        .where(*filters)
    )
    total = int(total_result.scalar_one())

    result = await db.execute(
        select(PublisherBlacklist, User.username, User.avatar_url)
        .join(User, User.id == PublisherBlacklist.blocked_user_id)
        .where(*filters)
        .order_by(
            PublisherBlacklist.created_at.desc(),
            PublisherBlacklist.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        _to_item(entry, username=username, avatar_url=avatar_url)
        for entry, username, avatar_url in result.all()
    ]
    return items, total


async def lookup_exact(
    db: AsyncSession,
    viewer: User,
    *,
    q: str,
) -> PublisherBlacklistLookupResponse:
    """按用户名或登录邮箱做完全匹配，只返回 1 人。

    不做用户 ID 枚举、不做前缀/模糊匹配、不返回候选列表；命中自己或管理员
    返回 400，查不到统一 404。
    """
    keyword = q.strip()
    result = await db.execute(
        select(User)
        .where(or_(User.username == keyword, User.email == keyword))
        .limit(1)
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=TARGET_NOT_FOUND_DETAIL,
        )
    if target.id == viewer.id or target.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=TARGET_FORBIDDEN_DETAIL,
        )
    return PublisherBlacklistLookupResponse(
        id=target.id,
        username=target.username,
        avatar_url=target.avatar_url,
    )


async def add_blacklist(
    db: AsyncSession,
    publisher: User,
    *,
    target_user_id: int,
    reason: str | None,
) -> PublisherBlacklistItem:
    """拉黑一个用户；重复拉黑幂等返回已有记录。"""
    # 锁发单人自己这一行，串行化“查重 + 计数 + 插入”，避免并发绕过 500 上限。
    # 只锁用户行，不碰订单/其它用户。
    await db.execute(
        select(User.id).where(User.id == publisher.id).with_for_update()
    )

    target_result = await db.execute(
        select(User).where(User.id == target_user_id)
    )
    target = target_result.scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=TARGET_NOT_FOUND_DETAIL,
        )
    if target.id == publisher.id or target.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=TARGET_FORBIDDEN_DETAIL,
        )
    if not target.is_active:
        # 禁用账号既不能接单，也不允许被拉黑（与「目标必须启用」口径一致）
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=TARGET_FORBIDDEN_DETAIL,
        )

    existing_result = await db.execute(
        select(PublisherBlacklist).where(
            PublisherBlacklist.publisher_id == publisher.id,
            PublisherBlacklist.blocked_user_id == target.id,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        # 幂等：不改已有备注，直接返回原记录
        return _to_item(
            existing, username=target.username, avatar_url=target.avatar_url
        )

    count_result = await db.execute(
        select(func.count(PublisherBlacklist.id)).where(
            PublisherBlacklist.publisher_id == publisher.id
        )
    )
    if int(count_result.scalar_one()) >= BLACKLIST_LIMIT_PER_PUBLISHER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=LIMIT_EXCEEDED_DETAIL,
        )

    entry = PublisherBlacklist(
        publisher_id=publisher.id,
        blocked_user_id=target.id,
        reason=_clean_reason(reason),
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    return _to_item(entry, username=target.username, avatar_url=target.avatar_url)


async def remove_blacklist(
    db: AsyncSession,
    publisher: User,
    *,
    blocked_user_id: int,
) -> None:
    """解除拉黑（物理删除）；未拉黑也视为成功，幂等。"""
    await db.execute(
        delete(PublisherBlacklist).where(
            PublisherBlacklist.publisher_id == publisher.id,
            PublisherBlacklist.blocked_user_id == blocked_user_id,
        )
    )


async def blacklisted_publisher_ids(
    db: AsyncSession,
    viewer_id: int,
) -> set[int]:
    """一次查询取回「拉黑了当前查看者」的全部发单人 ID。"""
    result = await db.execute(
        select(PublisherBlacklist.publisher_id).where(
            PublisherBlacklist.blocked_user_id == viewer_id
        )
    )
    return {int(publisher_id) for publisher_id in result.scalars().all()}


async def is_user_blacklisted(
    db: AsyncSession,
    publisher_id: int,
    blocked_user_id: int,
    *,
    for_update: bool = False,
) -> bool:
    """判断某用户是否被某发单人拉黑。

    ``for_update=True`` 用于接单持锁复核：MySQL REPEATABLE READ 下普通
    SELECT 会沿用本事务早先的快照，只有锁定读才能看到校验后被提交的拉黑。
    """
    query = (
        select(PublisherBlacklist.id)
        .where(
            PublisherBlacklist.publisher_id == publisher_id,
            PublisherBlacklist.blocked_user_id == blocked_user_id,
        )
        .limit(1)
    )
    if for_update:
        query = query.with_for_update()
    result = await db.execute(query)
    return result.scalar_one_or_none() is not None
