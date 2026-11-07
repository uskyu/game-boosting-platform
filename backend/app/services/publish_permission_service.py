"""Business logic for the self-service publish-permission workflow.

用户默认可能没有发单权限（新站），可以在站点允许时自助申请；管理员审核通过
后把 ``User.can_publish`` 置 true。所有状态变更都保留申请历史。
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.publish_permission_request import (
    PublishPermissionRequest,
    PublishPermissionStatus,
)
from app.models.user import User, UserRole
from app.schemas.publish_permission import (
    PublishPermissionMineResponse,
    PublishPermissionRequestItem,
)
from app.services.site_setting_service import get_or_create_site_setting


def _utcnow() -> datetime:
    """数据库统一存 naive UTC（项目约定）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _history_count_subquery():
    """每个用户的申请总数，一次聚合避免列表逐行 COUNT（N+1）。"""
    return (
        select(
            PublishPermissionRequest.user_id,
            func.count(PublishPermissionRequest.id).label("history_count"),
        )
        .group_by(PublishPermissionRequest.user_id)
        .subquery()
    )


def _to_item(
    request: PublishPermissionRequest,
    user: User,
    *,
    reviewer_name: str | None,
    history_count: int,
) -> PublishPermissionRequestItem:
    return PublishPermissionRequestItem(
        id=request.id,
        user_id=request.user_id,
        username=user.username,
        email=user.email,
        user_created_at=user.created_at,
        reason=request.reason,
        status=request.status,
        decision_reason=request.decision_reason,
        reviewed_by=request.reviewed_by,
        reviewed_by_name=reviewer_name,
        created_at=request.created_at,
        resolved_at=request.resolved_at,
        history_count=history_count,
    )


async def _reviewer_name(db: AsyncSession, reviewed_by: int | None) -> str | None:
    if reviewed_by is None:
        return None
    result = await db.execute(select(User.username).where(User.id == reviewed_by))
    return result.scalar_one_or_none()


async def _build_item(
    db: AsyncSession,
    request: PublishPermissionRequest,
    user: User,
    *,
    reviewer_name: str | None = None,
) -> PublishPermissionRequestItem:
    """构建返回项；history_count 为该用户全部申请数。"""
    count_result = await db.execute(
        select(func.count(PublishPermissionRequest.id)).where(
            PublishPermissionRequest.user_id == request.user_id
        )
    )
    history_count = int(count_result.scalar_one())
    if reviewer_name is None and request.reviewed_by is not None:
        reviewer_name = await _reviewer_name(db, request.reviewed_by)
    return _to_item(
        request,
        user,
        reviewer_name=reviewer_name,
        history_count=history_count,
    )


async def get_mine(db: AsyncSession, user: User) -> PublishPermissionMineResponse:
    """当前用户的发单权限状态 + 最近一次申请 + 是否已有待审核申请。"""
    setting = await get_or_create_site_setting(db)

    result = await db.execute(
        select(PublishPermissionRequest)
        .where(PublishPermissionRequest.user_id == user.id)
        .order_by(
            PublishPermissionRequest.created_at.desc(),
            PublishPermissionRequest.id.desc(),
        )
        .limit(1)
    )
    latest = result.scalar_one_or_none()

    pending_result = await db.execute(
        select(PublishPermissionRequest.id)
        .where(
            PublishPermissionRequest.user_id == user.id,
            PublishPermissionRequest.status == PublishPermissionStatus.PENDING,
        )
        .limit(1)
    )
    has_pending = pending_result.scalar_one_or_none() is not None

    latest_item = None
    if latest is not None:
        latest_item = await _build_item(db, latest, user)

    return PublishPermissionMineResponse(
        can_publish=bool(user.can_publish),
        apply_enabled=bool(setting.publish_permission_apply_enabled),
        latest=latest_item,
        has_pending=has_pending,
    )


async def apply(
    db: AsyncSession,
    user: User,
    reason: str | None,
) -> PublishPermissionRequestItem:
    """提交发单权限申请；用户行锁串行化重复提交。"""
    result = await db.execute(
        select(User).where(User.id == user.id).with_for_update()
    )
    locked_user = result.scalar_one_or_none()
    if locked_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    if locked_user.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="管理员无需申请发单权限",
        )
    if locked_user.can_publish:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="已具备发单权限",
        )

    setting = await get_or_create_site_setting(db)
    if not setting.publish_permission_apply_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未开启自助申请，请联系管理员",
        )

    # 必须用锁定读：MySQL REPEATABLE READ 下普通 SELECT 可能仍走旧快照，
    # 两个并发申请会在拿到用户锁后都看不到对方刚提交的 PENDING 而各插一条。
    pending_result = await db.execute(
        select(PublishPermissionRequest.id)
        .where(
            PublishPermissionRequest.user_id == locked_user.id,
            PublishPermissionRequest.status == PublishPermissionStatus.PENDING,
        )
        .limit(1)
        .with_for_update()
    )
    if pending_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已有待审核的发单权限申请",
        )

    clean_reason = (reason or "").strip() or None
    request = PublishPermissionRequest(
        user_id=locked_user.id,
        reason=clean_reason,
        status=PublishPermissionStatus.PENDING,
    )
    db.add(request)
    await db.flush()
    await db.refresh(request)
    return await _build_item(db, request, locked_user, reviewer_name=None)


async def withdraw(
    db: AsyncSession,
    user: User,
    request_id: int,
) -> PublishPermissionRequestItem:
    """撤回本人待审核的申请。"""
    result = await db.execute(
        select(PublishPermissionRequest)
        .where(PublishPermissionRequest.id == request_id)
        .with_for_update()
    )
    request = result.scalar_one_or_none()
    if request is None or request.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="申请不存在",
        )
    if request.status != PublishPermissionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该申请已处理，无法撤回",
        )

    request.status = PublishPermissionStatus.WITHDRAWN
    request.resolved_at = _utcnow()
    await db.flush()
    await db.refresh(request)
    return await _build_item(db, request, user)


async def list_admin(
    db: AsyncSession,
    status_filter: PublishPermissionStatus | None,
    q: str | None,
    page: int,
    page_size: int,
) -> tuple[list[PublishPermissionRequestItem], int]:
    """后台申请列表：join users，支持状态/关键词筛选与分页。"""
    reviewer = aliased(User)
    history = _history_count_subquery()

    filters = []
    if status_filter is not None:
        filters.append(PublishPermissionRequest.status == status_filter)
    if q and q.strip():
        keyword = q.strip()
        term = f"%{keyword}%"
        keyword_filter = (
            User.username.ilike(term)
            | User.email.ilike(term)
            | PublishPermissionRequest.reason.ilike(term)
        )
        # 后台搜索说明里承诺支持用户 ID：纯数字时同时按 ID 精确匹配
        if keyword.isdecimal():
            keyword_filter = keyword_filter | (User.id == int(keyword))
        filters.append(keyword_filter)

    total_result = await db.execute(
        select(func.count(PublishPermissionRequest.id))
        .select_from(PublishPermissionRequest)
        .join(User, User.id == PublishPermissionRequest.user_id)
        .where(*filters)
    )
    total = int(total_result.scalar_one())

    result = await db.execute(
        select(
            PublishPermissionRequest,
            User,
            reviewer.username.label("reviewer_name"),
            history.c.history_count,
        )
        .select_from(PublishPermissionRequest)
        .join(User, User.id == PublishPermissionRequest.user_id)
        .outerjoin(reviewer, reviewer.id == PublishPermissionRequest.reviewed_by)
        .join(history, history.c.user_id == PublishPermissionRequest.user_id)
        .where(*filters)
        .order_by(
            PublishPermissionRequest.created_at.desc(),
            PublishPermissionRequest.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items = [
        _to_item(
            request,
            request_user,
            reviewer_name=reviewer_name,
            history_count=int(history_count or 0),
        )
        for request, request_user, reviewer_name, history_count in result.all()
    ]
    return items, total


async def review(
    db: AsyncSession,
    admin: User,
    request_id: int,
    action: str,
    reason: str | None,
) -> PublishPermissionRequestItem:
    """管理员审核申请；approve 开通发单权限并通知申请人。"""
    result = await db.execute(
        select(PublishPermissionRequest)
        .where(PublishPermissionRequest.id == request_id)
        .with_for_update()
    )
    request = result.scalar_one_or_none()
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="申请不存在",
        )
    if request.status != PublishPermissionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该申请已处理",
        )

    clean_reason = (reason or "").strip()
    if action == "reject" and not clean_reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="驳回必须填写原因",
        )

    target_result = await db.execute(
        select(User).where(User.id == request.user_id).with_for_update()
    )
    target_user = target_result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="申请用户不存在",
        )

    now = _utcnow()
    if action == "approve":
        request.status = PublishPermissionStatus.APPROVED
        request.decision_reason = "审核通过"
        target_user.can_publish = True
    else:
        request.status = PublishPermissionStatus.REJECTED
        request.decision_reason = clean_reason

    request.reviewed_by = admin.id
    request.reviewed_at = now
    request.resolved_at = now
    await db.flush()
    await db.refresh(request)
    await db.refresh(target_user)

    # 函数内延迟导入，避免 service -> api 的模块级循环依赖。
    from app.api.notification_utils import notify_user
    from app.models.notification import NotificationType

    if action == "approve":
        await notify_user(
            db,
            user_id=target_user.id,
            type=NotificationType.APPLICATION_APPROVED,
            title="发单权限申请已通过",
            content="您的发单权限申请已通过，现在可以发布订单。",
            link="/publish-permission",
            ref_id=request.id,
        )
    else:
        await notify_user(
            db,
            user_id=target_user.id,
            type=NotificationType.APPLICATION_REJECTED,
            title="发单权限申请已驳回",
            content=f"您的发单权限申请已驳回：{clean_reason}",
            link="/publish-permission",
            ref_id=request.id,
        )

    return await _build_item(db, request, target_user, reviewer_name=admin.username)


async def auto_approve_pending_for_users(
    db: AsyncSession,
    admin_id: int,
    user_ids: list[int],
) -> int:
    """管理员直接开通发单权限时，把这些用户待审核的申请一并通过。

    批量场景可能涉及几百人，只做一条 UPDATE，不逐条通知。
    """
    if not user_ids:
        return 0
    now = _utcnow()
    result = await db.execute(
        update(PublishPermissionRequest)
        .where(
            PublishPermissionRequest.user_id.in_(user_ids),
            PublishPermissionRequest.status == PublishPermissionStatus.PENDING,
        )
        .values(
            status=PublishPermissionStatus.APPROVED,
            decision_reason="管理员直接开通",
            reviewed_by=admin_id,
            reviewed_at=now,
            resolved_at=now,
        )
    )
    return int(result.rowcount or 0)


async def auto_approve_pending_for_all_non_admin(
    db: AsyncSession,
    admin_id: int,
) -> int:
    """全量开通发单权限时，一次性通过所有启用非管理员的待审核申请。

    用户范围用子查询交给数据库，避免把全部用户 ID 物化到内存再拼 IN。
    """
    now = _utcnow()
    result = await db.execute(
        update(PublishPermissionRequest)
        .where(
            PublishPermissionRequest.user_id.in_(
                select(User.id).where(
                    User.role != UserRole.ADMIN,
                    User.is_active.is_(True),
                )
            ),
            PublishPermissionRequest.status == PublishPermissionStatus.PENDING,
        )
        .values(
            status=PublishPermissionStatus.APPROVED,
            decision_reason="管理员直接开通",
            reviewed_by=admin_id,
            reviewed_at=now,
            resolved_at=now,
        )
    )
    return int(result.rowcount or 0)
