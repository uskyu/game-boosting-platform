"""
Utility helpers for creating and pushing notifications from API endpoints.
Keeps the endpoint code concise while ensuring every state change
triggers both a DB notification record and a real-time WebSocket push.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
            # 同 notification_service.create：避免 flush 后懒加载 IO
            created_at=datetime.now(timezone.utc),
        ))
    if not notifications:
        return 0

    db.add_all(notifications)
    await db.flush()

    # 在线打手实时推送：登记到事务提交之后并发执行（尽力而为，失败不影响
    # 已落库的通知）。100 人在线时这里是全站扇出的大头，绝不能留在业务
    # 事务里拖住订单行锁和连接池。
    cm = get_connection_manager()
    payloads = [
        (notification.user_id, NotificationResponse.model_validate(notification).model_dump(mode="json"))
        for notification in notifications
    ]

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
        len(notifications),
    )
    return len(notifications)
