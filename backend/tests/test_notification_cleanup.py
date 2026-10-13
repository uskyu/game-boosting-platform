"""Expired-notification cleanup behaviour."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.notification import Notification, NotificationType
from app.models.user import User
from app.services.notification_cleanup import cleanup_expired_notifications


async def _make_user(session) -> User:
    user = User(
        email="cleanup-owner@example.com",
        username="CleanupOwner",
        hashed_password="not-used",
    )
    session.add(user)
    await session.flush()
    return user


async def _make_notification(session, *, user_id: int, age_days: int) -> None:
    session.add(Notification(
        user_id=user_id,
        type=NotificationType.SYSTEM_ANNOUNCEMENT,
        title="新订单发布",
        content="expired cleanup fixture",
        link="/orders/1",
        ref_id=1,
        is_read=False,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=age_days),
    ))


async def test_cleanup_removes_only_expired_notifications(db_session):
    """保留期内的通知留下，过期的删掉。"""
    user = await _make_user(db_session)
    await _make_notification(db_session, user_id=user.id, age_days=200)
    await _make_notification(db_session, user_id=user.id, age_days=120)
    await _make_notification(db_session, user_id=user.id, age_days=89)
    await _make_notification(db_session, user_id=user.id, age_days=1)
    await db_session.commit()

    deleted = await cleanup_expired_notifications(
        db_session, retention_days=90, batch_size=10
    )

    assert deleted == 2
    remaining = (await db_session.execute(select(func.count(Notification.id)))).scalar()
    assert remaining == 2


async def test_cleanup_keeps_non_broadcast_notifications(db_session):
    """过期也不删其他类型通知和系统公告：只清「新订单发布」广播。"""
    user = await _make_user(db_session)
    # 过期的新订单广播：应删
    await _make_notification(db_session, user_id=user.id, age_days=400)
    # 过期的其他类型通知：必须保留
    db_session.add(Notification(
        user_id=user.id,
        type=NotificationType.ORDER_ACCEPTED,
        title="订单已被接手",
        content="keep me",
        link="/orders/1",
        ref_id=1,
        is_read=False,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    ))
    # 过期的系统公告（link 不指向订单）：必须保留
    db_session.add(Notification(
        user_id=user.id,
        type=NotificationType.SYSTEM_ANNOUNCEMENT,
        title="系统公告",
        content="keep me too",
        link="/announcements",
        ref_id=None,
        is_read=False,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    ))
    await db_session.commit()

    deleted = await cleanup_expired_notifications(
        db_session, retention_days=7, batch_size=10
    )

    assert deleted == 1
    remaining_types = list((await db_session.execute(
        select(Notification.type, Notification.title).order_by(Notification.id)
    )).all())
    assert (NotificationType.ORDER_ACCEPTED, "订单已被接手") in remaining_types
    assert (NotificationType.SYSTEM_ANNOUNCEMENT, "系统公告") in remaining_types


async def test_cleanup_respects_batch_size_and_is_idempotent(db_session):
    """分批删除：单批删满就继续，删不满即停；重复执行不再删除。"""
    user = await _make_user(db_session)
    for index in range(7):
        await _make_notification(db_session, user_id=user.id, age_days=300 + index)
    await db_session.commit()

    first = await cleanup_expired_notifications(
        db_session, retention_days=90, batch_size=3
    )
    assert first == 7

    second = await cleanup_expired_notifications(
        db_session, retention_days=90, batch_size=3
    )
    assert second == 0


async def test_cleanup_disabled_when_retention_not_positive(db_session):
    """retention_days <= 0 表示关闭清理，绝不动数据。"""
    user = await _make_user(db_session)
    await _make_notification(db_session, user_id=user.id, age_days=999)
    await db_session.commit()

    deleted = await cleanup_expired_notifications(
        db_session, retention_days=0, batch_size=10
    )

    assert deleted == 0
    remaining = (await db_session.execute(select(func.count(Notification.id)))).scalar()
    assert remaining == 1
