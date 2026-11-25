"""Expired-notification cleanup behaviour."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.notification import Notification, NotificationType
from app.models.user import User
from app.services.notification_cleanup import cleanup_expired_notifications


@pytest.fixture
def no_cleanup_sleep(monkeypatch) -> AsyncMock:
    """cleanup 模块 `import asyncio` 后用 asyncio.sleep，这里替换成 AsyncMock。"""
    sleep_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.notification_cleanup.asyncio.sleep", sleep_mock
    )
    return sleep_mock


def test_notification_retention_defaults():
    """老板拍板：默认保留期 3 天 -> 2 天、清理批次 5000 -> 2000（环境变量仍可覆盖）。"""
    assert settings.NOTIFICATION_RETENTION_DAYS == 2
    assert settings.NOTIFICATION_CLEANUP_BATCH_SIZE == 2000
    assert settings.NOTIFICATION_CLEANUP_INTERVAL_SECONDS == 6 * 3600


async def _make_user(session) -> User:
    user = User(
        email="cleanup-owner@example.com",
        username="CleanupOwner",
        hashed_password="not-used",
    )
    session.add(user)
    await session.flush()
    return user


def _utc_days_ago(age_days: int) -> datetime:
    """显式 UTC 时间（ORM 存 naive UTC），并远离保留期边界。"""
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=age_days)


async def _make_notification(
    session,
    *,
    user_id: int,
    notification_type: NotificationType,
    link: str | None,
    age_days: int,
    title: str,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        type=notification_type,
        title=title,
        content="expired cleanup fixture",
        link=link,
        ref_id=None,
        is_read=False,
        created_at=_utc_days_ago(age_days),
    )
    session.add(notification)
    return notification


async def test_cleanup_removes_only_expired_new_order_broadcasts(
    db_session, no_cleanup_sleep
):
    """小批次清理只删过期的新单广播；其他类型、未到期广播、真系统公告都保留。"""
    user = await _make_user(db_session)

    # 目标：过期的新单广播 5 条。batch_size=2 会拆成 2/2/1 三批，覆盖循环退出。
    targets = [
        await _make_notification(
            db_session,
            user_id=user.id,
            notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
            link=f"/orders/{index + 1}",
            age_days=10 + index,
            title=f"新订单发布 {index + 1}",
        )
        for index in range(5)
    ]

    # 保留：过期的其他类型通知（link 同样指向订单也不能误删）。
    kept_other_types = [
        await _make_notification(
            db_session,
            user_id=user.id,
            notification_type=NotificationType.ORDER_ACCEPTED,
            link="/orders/99",
            age_days=10,
            title="订单已被接手",
        ),
        await _make_notification(
            db_session,
            user_id=user.id,
            notification_type=NotificationType.ORDER_CANCELLED,
            link="/orders/99",
            age_days=10,
            title="订单已取消",
        ),
        await _make_notification(
            db_session,
            user_id=user.id,
            notification_type=NotificationType.NEW_MESSAGE,
            link=None,
            age_days=10,
            title="新聊天消息",
        ),
    ]

    # 保留：未到期的广播（1 天 < 保留期 2 天）。
    kept_fresh = [
        await _make_notification(
            db_session,
            user_id=user.id,
            notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
            link=f"/orders/{index + 100}",
            age_days=1,
            title=f"未到期的新订单发布 {index + 1}",
        )
        for index in range(2)
    ]

    # 保留：过期但 link 不是 /orders/ 的真系统公告。
    kept_announcements = [
        await _make_notification(
            db_session,
            user_id=user.id,
            notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
            link="/announcements/7",
            age_days=30,
            title="真正的系统公告",
        ),
        await _make_notification(
            db_session,
            user_id=user.id,
            notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
            link=None,
            age_days=30,
            title="无链接系统公告",
        ),
    ]
    await db_session.commit()

    target_ids = {notification.id for notification in targets}
    kept_ids = {
        notification.id
        for notification in kept_other_types + kept_fresh + kept_announcements
    }

    deleted = await cleanup_expired_notifications(
        db_session, retention_days=2, batch_size=2
    )

    # e) 返回删除总数正确。
    assert deleted == len(targets)

    remaining_ids = set(
        (await db_session.execute(select(Notification.id))).scalars().all()
    )
    # a) 目标行全删；b) 其他类型一条不删；c) 未到期广播一条不删；
    # d) 非订单 link 的 SYSTEM_ANNOUNCEMENT 一条不删。
    assert target_ids.isdisjoint(remaining_ids)
    assert remaining_ids == kept_ids

    # 批间让路：5 条按每批 2 条删，至少在批间 sleep 过一次且间隔正确。
    assert no_cleanup_sleep.await_count >= 1
    no_cleanup_sleep.assert_any_await(0.15)


async def test_cleanup_respects_batch_size_and_is_idempotent(
    db_session, no_cleanup_sleep
):
    """分批删除：单批删满就继续，删不满即停；重复执行不再删除。"""
    user = await _make_user(db_session)
    for index in range(7):
        await _make_notification(
            db_session,
            user_id=user.id,
            notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
            link=f"/orders/{index + 1}",
            age_days=300 + index,
            title=f"过期的新订单发布 {index + 1}",
        )
    await db_session.commit()

    first = await cleanup_expired_notifications(
        db_session, retention_days=2, batch_size=3
    )
    assert first == 7

    second = await cleanup_expired_notifications(
        db_session, retention_days=2, batch_size=3
    )
    assert second == 0


async def test_cleanup_disabled_when_retention_not_positive(
    db_session, no_cleanup_sleep
):
    """retention_days <= 0 表示关闭清理，绝不动数据。"""
    user = await _make_user(db_session)
    await _make_notification(
        db_session,
        user_id=user.id,
        notification_type=NotificationType.SYSTEM_ANNOUNCEMENT,
        link="/orders/1",
        age_days=999,
        title="过期的新订单发布",
    )
    await db_session.commit()

    deleted = await cleanup_expired_notifications(
        db_session, retention_days=0, batch_size=10
    )

    assert deleted == 0
    remaining = (await db_session.execute(select(func.count(Notification.id)))).scalar()
    assert remaining == 1
