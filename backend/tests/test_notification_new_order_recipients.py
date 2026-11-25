"""「新订单发布」广播收件人资格：被禁止接单的用户不再收到广播。"""

from decimal import Decimal

from sqlalchemy import select

from app.api.notification_utils import notify_boosters_new_order, notify_user
from app.models.notification import Notification, NotificationType, UserPreference
from app.models.order import Order
from app.models.publisher_blacklist import PublisherBlacklist
from app.models.user import User, UserRole
from app.services.connection_manager import connection_manager


def _set_online_users(monkeypatch, *user_ids: int) -> None:
    """把连接管理器的在线快照固定为本用例的候选集合。

    公开单现在只给在线用户写通知；本文件验证的是「在线之后」的资格过滤，
    因此把候选全部置为在线，离线不发由 test_new_order_notification_pipeline
    单独覆盖。
    """
    online_ids = {int(user_id) for user_id in user_ids}

    async def _online_user_ids() -> set[int]:
        return set(online_ids)

    monkeypatch.setattr(connection_manager, "online_user_ids", _online_user_ids)


async def _make_user(
    db_session,
    *,
    name: str,
    role: UserRole = UserRole.BOOSTER,
    is_active: bool = True,
    can_accept: bool | None = True,
) -> User:
    """建测试用户；can_accept=None 表示沿用模型默认值。"""
    user = User(
        email=f"{name}@example.com",
        username=name,
        hashed_password="not-used",
        role=role,
        is_active=is_active,
    )
    if can_accept is not None:
        user.can_accept = can_accept
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_order(db_session, publisher_id: int) -> Order:
    order = Order(
        user_id=publisher_id,
        game_name="三角洲行动",
        current_rank="黄金",
        target_rank="钻石",
        price=Decimal("300.00"),
    )
    db_session.add(order)
    await db_session.commit()
    # created_at 是 server_default：commit 后属性未加载，直接读取会触发异步
    # 惰性 IO（MissingGreenlet）。notify 需要快照该字段，测试里显式回填。
    await db_session.refresh(order)
    return order


async def _notified_user_ids(db_session, order_id: int) -> list[int]:
    result = await db_session.execute(
        select(Notification.user_id).where(
            Notification.ref_id == order_id,
            Notification.type == NotificationType.SYSTEM_ANNOUNCEMENT,
        )
    )
    return [int(user_id) for user_id in result.scalars().all()]


async def test_active_non_admin_with_can_accept_receives_new_order(
    db_session, monkeypatch
):
    """can_accept=True（含未显式赋值走默认值）的活跃非管理员都收到通知。"""
    publisher = await _make_user(
        db_session, name="notif-allow-publisher", role=UserRole.ADMIN
    )
    # 显式 True 与模型默认值各一人，确保默认值用户不会漏发
    explicit = await _make_user(db_session, name="notif-allow-explicit", can_accept=True)
    defaulted = await _make_user(db_session, name="notif-allow-default", can_accept=None)
    ineligible = await _make_user(
        db_session, name="notif-allow-ineligible", can_accept=False
    )
    order = await _make_order(db_session, publisher.id)
    # 候选全部在线：验证 can_accept 过滤，而不是在线裁剪
    _set_online_users(
        monkeypatch, publisher.id, explicit.id, defaulted.id, ineligible.id
    )

    count = await notify_boosters_new_order(
        db_session, order=order, exclude_user_id=publisher.id
    )

    assert count == 2
    # notify 走独立后台 session 并已 commit；先结束本会话的只读事务，
    # 否则 REPEATABLE READ 快照看不到新提交的行。
    await db_session.commit()
    notified = await _notified_user_ids(db_session, order.id)
    assert set(notified) == {explicit.id, defaulted.id}
    assert len(notified) == 2
    assert ineligible.id not in notified


async def test_new_order_notifications_keep_other_exclusions(db_session, monkeypatch):
    """inactive / ADMIN / 黑名单 / 偏好关闭 / exclude_user_id 仍然排除。"""
    publisher = await _make_user(
        db_session, name="notif-excl-publisher", role=UserRole.ADMIN
    )
    eligible = await _make_user(db_session, name="notif-excl-eligible")
    inactive = await _make_user(
        db_session, name="notif-excl-inactive", is_active=False, can_accept=True
    )
    admin = await _make_user(
        db_session, name="notif-excl-admin", role=UserRole.ADMIN, can_accept=True
    )
    blacklisted = await _make_user(db_session, name="notif-excl-blacklisted")
    pref_off = await _make_user(db_session, name="notif-excl-pref-off")
    excluded = await _make_user(db_session, name="notif-excl-by-param")

    db_session.add(
        PublisherBlacklist(publisher_id=publisher.id, blocked_user_id=blacklisted.id)
    )
    db_session.add(
        UserPreference(
            user_id=pref_off.id,
            notification_settings={NotificationType.SYSTEM_ANNOUNCEMENT.value: False},
        )
    )
    await db_session.flush()
    order = await _make_order(db_session, publisher.id)
    # 候选全部在线：验证其余资格过滤仍生效
    _set_online_users(
        monkeypatch,
        publisher.id,
        eligible.id,
        inactive.id,
        admin.id,
        blacklisted.id,
        pref_off.id,
        excluded.id,
    )

    count = await notify_boosters_new_order(
        db_session, order=order, exclude_user_id=excluded.id
    )

    assert count == 1
    # 同上：后台 session 已提交，先结束本会话只读事务再查询。
    await db_session.commit()
    notified = await _notified_user_ids(db_session, order.id)
    assert set(notified) == {eligible.id}
    assert len(notified) == 1
    for user in (inactive, admin, blacklisted, pref_off, excluded):
        assert user.id not in notified


async def test_can_accept_false_user_still_gets_other_notification_types(db_session):
    """仅在「新订单发布」广播加过滤；其他通知类型不受 can_accept 影响。"""
    user = await _make_user(db_session, name="notif-other-type", can_accept=False)

    await notify_user(
        db_session,
        user_id=user.id,
        type=NotificationType.ORDER_DELIVERED,
        title="代练提交完成",
        content="can_accept=False 的用户仍应收到非新订单通知",
        link="/orders/1",
        ref_id=1,
    )

    rows = (
        await db_session.execute(
            select(Notification).where(Notification.user_id == user.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].type == NotificationType.ORDER_DELIVERED
