from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_async_session
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.schemas.push import PushSubscriptionCreate, PushSubscriptionDelete, PushSubscriptionResponse
router = APIRouter(prefix="/push", tags=["push"])
@router.get("/public-key")
async def public_key():
    return {"public_key": settings.PUSH_VAPID_PUBLIC_KEY}
@router.post("/subscribe", response_model=PushSubscriptionResponse)
async def subscribe(data: PushSubscriptionCreate, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[AsyncSession, Depends(get_async_session)]):
    result = await db.execute(select(PushSubscription).where(PushSubscription.endpoint == data.endpoint))
    item = result.scalar_one_or_none()
    if item:
        item.user_id = current_user.id
        item.p256dh, item.auth, item.expiration_time, item.enabled = data.p256dh, data.auth, data.expiration_time, True
    else:
        item = PushSubscription(user_id=current_user.id, **data.model_dump())
        db.add(item)
    await db.flush()
    return item
@router.delete("/subscribe")
async def unsubscribe(data: PushSubscriptionDelete, current_user: Annotated[User, Depends(get_current_user)], db: Annotated[AsyncSession, Depends(get_async_session)]):
    result = await db.execute(select(PushSubscription).where(PushSubscription.endpoint == data.endpoint, PushSubscription.user_id == current_user.id))
    if (item := result.scalar_one_or_none()) is not None:
        await db.delete(item)
    return {"ok": True}
