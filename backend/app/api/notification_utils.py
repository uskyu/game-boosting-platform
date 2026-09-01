"""
Utility helpers for creating and pushing notifications from API endpoints.
Keeps the endpoint code concise while ensuring every state change
triggers both a DB notification record and a real-time WebSocket push.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import NotificationType
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
