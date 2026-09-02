"""
Utility helpers for creating and pushing notifications from API endpoints.
Keeps the endpoint code concise while ensuring every state change
triggers both a DB notification record and a real-time WebSocket push.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType, UserPreference
from app.models.order import Order
from app.models.user import User, UserRole
from app.schemas.notification import NotificationResponse
from app.services.connection_manager import get_connection_manager
from app.services.notification_service import get_notification_service

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

    # Real-time push
    cm = get_connection_manager()
    try:
        payload = NotificationResponse.model_validate(notification).model_dump(mode="json")
        await cm.send_notification(user_id, payload)
    except Exception:
        logger.debug("WebSocket push failed for user %s, notification saved to DB", user_id)


async def notify_boosters_new_order(
    db: AsyncSession,
    *,
    order: Order,
    exclude_user_id: int | None = None,
) -> int:
    """
    管理员（老板）发布订单后，向所有活跃打手（BOOSTER）广播"新订单"通知。

    轻量批量实现（复用现有通知机制）：
    - 一次查询目标打手与通知偏好（偏好记录只读，缺失视为默认开启）；
    - 逐条校验偏好后批量 db.add_all + 单次 flush —— 全部写入发生在
      同一事务内，不在循环里逐条 commit；
    - 对在线打手尽力做 WebSocket 实时推送（失败不影响落库）。

    Returns:
        实际写入的通知条数。
    """
    # 平台模型：管理员发单，其余注册用户均为打手，全部纳入通知范围
    result = await db.execute(
        select(User.id).where(
            User.role != UserRole.ADMIN,
            User.is_active.is_(True),
        )
    )
    booster_ids = [int(uid) for uid in result.scalars().all()]
    if exclude_user_id is not None:
        booster_ids = [uid for uid in booster_ids if uid != exclude_user_id]
    if not booster_ids:
        return 0

    # 通知偏好只读校验（缺失偏好记录视为全部开启，避免逐用户懒建写库）
    prefs_result = await db.execute(
        select(UserPreference).where(UserPreference.user_id.in_(booster_ids))
    )
    settings = {}
    for pref in prefs_result.scalars().all():
        settings[pref.user_id] = pref.notification_settings

    title = "新订单发布"
    content = f"管理员发布了新订单「{order.game_name}」，请前往抢单"
    link = f"/orders/{order.id}"

    notifications = []
    for uid in booster_ids:
        pref = settings.get(uid)
        if pref is not None and not pref.get(NotificationType.SYSTEM_ANNOUNCEMENT.value, True):
            continue
        notifications.append(Notification(
            user_id=uid,
            type=NotificationType.SYSTEM_ANNOUNCEMENT,
            title=title,
            content=content,
            link=link,
            ref_id=order.id,
        ))
    if not notifications:
        return 0

    db.add_all(notifications)
    await db.flush()

    # 在线打手实时推送（尽力而为，失败不影响已落库的通知）
    cm = get_connection_manager()
    for notification in notifications:
        try:
            payload = NotificationResponse.model_validate(notification).model_dump(mode="json")
            await cm.send_notification(notification.user_id, payload)
        except Exception:
            logger.debug("WebSocket push failed for booster %s", notification.user_id)

    logger.info(
        "Broadcast new-order notification for order %s to %s boosters",
        order.id,
        len(notifications),
    )
    return len(notifications)
