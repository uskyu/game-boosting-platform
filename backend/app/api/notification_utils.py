"""
Utility helpers for creating and pushing notifications from API endpoints.
Keeps the endpoint code concise while ensuring every state change
triggers both a DB notification record and a real-time WebSocket push.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.request_timing import (
    STAGE_NOTIFICATION_INSERT,
    STAGE_NOTIFICATION_PAYLOADS,
    STAGE_NOTIFICATION_PREFERENCES,
    STAGE_NOTIFICATION_PREPARE,
    STAGE_NOTIFICATION_RECIPIENTS,
    TIMING_STATE_KEY,
    measure_stage,
)

from app.core.post_commit import run_after_commit
from app.models.notification import Notification, NotificationType, UserPreference
from app.models.order import Order
from app.models.user import User, UserRole
from app.schemas.notification import NotificationResponse
from app.services.connection_manager import get_connection_manager
from app.services.notification_service import get_notification_service
from app.services.web_push import push_user_notification

logger = logging.getLogger(__name__)


async def notify_user(
    db: AsyncSession,
    *,
    user_id: int,
    type: NotificationType,
    title: str,
    content: str,
    link: str | None = None,
    ref_id: int | None = None,
) -> None:
    """Create a notification in DB and push it via WebSocket if user is online."""
    svc = get_notification_service(db)

    # Check user preference
    if not await svc.should_notify(user_id, type):
        return

    notification = await svc.create(
        user_id=user_id,
        type=type,
        title=title,
        content=content,
        link=link,
        ref_id=ref_id,
    )

    # Real-time push：登记到事务提交之后执行，WebSocket 扇出不占业务事务
    cm = get_connection_manager()
    payload = NotificationResponse.model_validate(notification).model_dump(mode="json")

    async def _push_ws() -> None:
        try:
            await cm.send_notification(user_id, payload)
        except Exception:
            logger.debug("WebSocket push failed for user %s, notification saved to DB", user_id)

    await run_after_commit(_push_ws)

    should_push = (type == NotificationType.SYSTEM_ANNOUNCEMENT and "新订单" in title) or type in (NotificationType.ORDER_ACCEPTED, NotificationType.NEW_MESSAGE)
    if should_push:
        try:
            await push_user_notification(db, user_id, payload)
        except Exception:
            logger.debug("Web Push failed for user %s", user_id, exc_info=True)


async def notify_boosters_new_order(
    db: AsyncSession,
    *,
    order: Order,
    exclude_user_id: int | None = None,
    request: Request | None = None,
    order_card: dict | None = None,
) -> int:
    """
    管理员（老板）发布订单后，向所有活跃非管理员用户发送"新订单"通知。

    收件人和偏好通过集合查询，通知使用单条多值 INSERT 写入当前事务；
    在线 WebSocket 推送仍在事务提交后尽力执行。

    Args:
        order_card: 大厅列表卡片的 slim dict，由调用方构建并复用，用于让前端
            收到新单通知后本地插卡、避免全量刷新。不为 None 时附加到每条
            WebSocket payload 的 "order_card" 键上（多份 payload 共享同一个
            只读 dict 引用，不做深拷贝）；传 None 时行为与历史完全一致。

    Returns:
        实际写入的通知条数。
    """
    async def measured(stage_name: str, operation):
        if request is None:
            return await operation()
        async with measure_stage(request, stage_name):
            return await operation()

    def record_count(name: str, value: int) -> None:
        if request is None:
            return
        counts = getattr(request.state, TIMING_STATE_KEY + "_counts", None)
        if counts is None:
            counts = {}
            setattr(request.state, TIMING_STATE_KEY + "_counts", counts)
        counts[name] = max(0, int(value))

    # 平台模型：管理员发单，其余注册用户均为打手，全部纳入通知范围
    async def select_recipients():
        result = await db.execute(
            select(User.id).where(
                User.role != UserRole.ADMIN,
                User.is_active.is_(True),
            )
        )
        recipient_ids = [int(uid) for uid in result.scalars().all()]
        if exclude_user_id is not None:
            recipient_ids = [uid for uid in recipient_ids if uid != exclude_user_id]
        return recipient_ids

    booster_ids = await measured(STAGE_NOTIFICATION_RECIPIENTS, select_recipients)
    record_count("recipient_count", len(booster_ids))
    if not booster_ids:
        return 0

    # 通知偏好只读校验（缺失偏好记录视为全部开启，避免逐用户懒建写库）
    async def select_preferences():
        prefs_result = await db.execute(
            select(UserPreference).where(UserPreference.user_id.in_(booster_ids))
        )
        return {
            pref.user_id: pref.notification_settings
            for pref in prefs_result.scalars().all()
        }

    settings = await measured(STAGE_NOTIFICATION_PREFERENCES, select_preferences)
    record_count("preference_count", len(settings))

    title = "新订单发布"
    content = f"管理员发布了新订单「{order.game_name}」，请前往抢单"
    link = f"/orders/{order.id}"

    async def prepare_notification_values():
        created_at = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
        values = []
        for uid in booster_ids:
            pref = settings.get(uid)
            if pref is not None and not pref.get(NotificationType.SYSTEM_ANNOUNCEMENT.value, True):
                continue
            values.append({
                "user_id": uid,
                "type": NotificationType.SYSTEM_ANNOUNCEMENT,
                "title": title,
                "content": content,
                "link": link,
                "ref_id": order.id,
                "created_at": created_at,
                "is_read": False,
            })
        return created_at, values

    created_at, values = await measured(
        STAGE_NOTIFICATION_PREPARE,
        prepare_notification_values,
    )
    record_count("notification_count", len(values))
    if not values:
        return 0

    async def insert_notifications():
        inserted_count = 0
        for offset in range(0, len(values), 500):
            result = await db.execute(
                insert(Notification).values(values[offset:offset + 500])
            )
            inserted_count += result.rowcount or 0
        return inserted_count

    inserted_count = await measured(STAGE_NOTIFICATION_INSERT, insert_notifications)
    if inserted_count != len(values):
        raise RuntimeError("Unable to persist all order notifications")

    async def build_payloads():
        # 回读只按 ref_id + type 定位本单发出的通知（ix_notifications_ref_id），
        # 不再附带 title/created_at 等无索引条件——那会让 MySQL 退回 user_id
        # 索引扫几十万行历史通知，表越大越慢。
        result = await db.execute(
            select(Notification.id, Notification.user_id, Notification.created_at).where(
                Notification.ref_id == order.id,
                Notification.type == NotificationType.SYSTEM_ANNOUNCEMENT,
            )
        )
        inserted = result.all()
        if len(inserted) != len(values):
            raise RuntimeError("Unable to reload all persisted order notifications")
        payloads: list[tuple[int, dict]] = []
        for notification_id, user_id, notification_created_at in inserted:
            payload = NotificationResponse(
                id=notification_id,
                user_id=user_id,
                type=NotificationType.SYSTEM_ANNOUNCEMENT,
                title=title,
                content=content,
                link=link,
                ref_id=order.id,
                is_read=False,
                created_at=notification_created_at,
                read_at=None,
            ).model_dump(mode="json")
            if order_card is not None:
                # 全站扇出共享同一个只读卡片引用，避免逐条深拷贝
                payload["order_card"] = order_card
            payloads.append((user_id, payload))
        return payloads

    payloads = await measured(STAGE_NOTIFICATION_PAYLOADS, build_payloads)

    # 在线打手实时推送：登记到事务提交之后并发执行（尽力而为，失败不影响
    # 已落库的通知）。100 人在线时这里是全站扇出的大头，绝不能留在业务
    # 事务里拖住订单行锁和连接池。
    cm = get_connection_manager()

    async def _broadcast() -> None:
        results = await asyncio.gather(
            *(cm.send_notification(uid, payload) for uid, payload in payloads),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.debug("WebSocket push failed during new-order broadcast: %r", result)

    await run_after_commit(_broadcast)

    logger.info(
        "Broadcast new-order notification for order %s to %s boosters",
        order.id,
        len(payloads),
    )
    return len(payloads)
