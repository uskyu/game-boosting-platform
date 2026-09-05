import json
import logging
from app.core.config import settings
from app.models.push_subscription import PushSubscription
logger = logging.getLogger(__name__)
async def send_web_push(db, subscription: PushSubscription, payload: dict) -> bool:
    if not settings.PUSH_ENABLED or not settings.PUSH_VAPID_PUBLIC_KEY or not settings.PUSH_VAPID_PRIVATE_KEY:
        return False
    try:
        from pywebpush import webpush
        webpush(subscription_info={"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}}, data=json.dumps(payload), vapid_private_key=settings.PUSH_VAPID_PRIVATE_KEY, vapid_claims={"sub": settings.PUSH_VAPID_SUBJECT})
        return True
    except Exception as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) in (404, 410):
            await db.delete(subscription)
        else:
            logger.debug("Web Push failed", exc_info=True)
        return False
async def push_user_notification(db, user_id: int, payload: dict) -> None:
    from sqlalchemy import select
    result = await db.execute(select(PushSubscription).where(PushSubscription.user_id == user_id, PushSubscription.enabled.is_(True)))
    for subscription in result.scalars().all():
        await send_web_push(db, subscription, payload)
