"""活跃通知轮询登记表 + 公开新单兜底收件人测试。

覆盖：
- registry 的 touch / 窗口过滤 / 过期清理 / reset；
- 公开单：WS 在线集合为空时，最近轮询过通知的 eligible 用户仍能落通知行；
- 指定单：仍只给 designated_booster_id，不查 registry；
- registry 故障时退回仅 WS 在线集合，不影响发单；
- GET /notifications 与 /notifications/unread-count 会登记当前用户。
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.api.notification_utils import notify_boosters_new_order
from app.models.notification import Notification, NotificationType
from app.models.order import Order
from app.models.user import User, UserRole
from app.services import active_user_registry
from app.services.connection_manager import connection_manager
from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# registry 单元行为
# ---------------------------------------------------------------------------


async def _recent(user_id: int, window: float | None = None) -> bool:
    window = (
        active_user_registry.DEFAULT_ACTIVE_WINDOW_SECONDS
        if window is None
        else window
    )
    return user_id in await active_user_registry.recent_user_ids(window)


async def test_touch_records_user_and_recent_filters_by_window(monkeypatch):
    active_user_registry.reset()
    clock = [100.0]
    monkeypatch.setattr(active_user_registry, "monotonic", lambda: clock[0])

    await active_user_registry.touch(101)
    clock[0] = 110.0
    await active_user_registry.touch(202)

    # 5 秒窗口只包含最近一次；120 秒窗口两者都在。
    assert await active_user_registry.recent_user_ids(5) == {202}
    assert await active_user_registry.recent_user_ids(120) == {101, 202}


async def test_touch_prunes_entries_older_than_default_window(monkeypatch):
    active_user_registry.reset()
    clock = [100.0]
    monkeypatch.setattr(active_user_registry, "monotonic", lambda: clock[0])

    await active_user_registry.touch(101)
    clock[0] = 300.0
    await active_user_registry.touch(202)

    # 101 已超过默认窗口，下一次 touch 时应被物理清理；更大的查询窗口
    # 也不应再看到它。
    assert 101 not in active_user_registry._touch_times
    assert 202 in active_user_registry._touch_times
    assert await active_user_registry.recent_user_ids(1000) == {202}


async def test_recent_user_ids_prunes_expired_entries(monkeypatch):
    active_user_registry.reset()
    clock = [100.0]
    monkeypatch.setattr(active_user_registry, "monotonic", lambda: clock[0])

    await active_user_registry.touch(101)
    clock[0] = 250.0

    assert await active_user_registry.recent_user_ids(120) == set()
    assert active_user_registry._touch_times == {}


async def test_reset_clears_registry(monkeypatch):
    active_user_registry.reset()
    await active_user_registry.touch(101)
    assert await _recent(101)

    active_user_registry.reset()
    assert await active_user_registry.recent_user_ids(120) == set()
    assert active_user_registry._touch_times == {}


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


async def _make_user(
    db_session,
    *,
    name: str,
    role: UserRole = UserRole.BOOSTER,
    is_active: bool = True,
    can_accept: bool = True,
) -> User:
    user = User(
        email=f"{name}@example.com",
        username=name,
        hashed_password="not-used",
        role=role,
        is_active=is_active,
        can_accept=can_accept,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_order(
    db_session,
    publisher_id: int,
    *,
    designated_booster_id: int | None = None,
) -> Order:
    order = Order(
        user_id=publisher_id,
        designated_booster_id=designated_booster_id,
        game_name="三角洲行动",
        current_rank="黄金",
        target_rank="钻石",
        price=Decimal("300.00"),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(order)
    await db_session.commit()
    return order


def _record_ws_sends(monkeypatch) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []

    async def _record(user_id: int, notification: dict) -> None:
        recorded.append({"user_id": user_id, "payload": notification})

    monkeypatch.setattr(connection_manager, "send_notification", _record)
    return recorded


def _set_online_users(monkeypatch, *user_ids: int) -> None:
    online_ids = {int(user_id) for user_id in user_ids}

    async def _online_user_ids() -> set[int]:
        return set(online_ids)

    monkeypatch.setattr(connection_manager, "online_user_ids", _online_user_ids)


async def _notified_user_ids(db_session, order_id: int) -> list[int]:
    result = await db_session.execute(
        select(Notification.user_id).where(
            Notification.ref_id == order_id,
            Notification.type == NotificationType.SYSTEM_ANNOUNCEMENT,
        )
    )
    return [int(user_id) for user_id in result.scalars().all()]


# ---------------------------------------------------------------------------
# 公开单 / 指定单 fanout
# ---------------------------------------------------------------------------


async def test_public_order_notifies_recent_poller_when_ws_reports_offline(
    db_session, monkeypatch
):
    """手机 WS 半开被判离线：只要最近轮询过通知，仍应落一条通知行。"""
    publisher = await _make_user(
        db_session, name="aur-pub-publisher", role=UserRole.ADMIN
    )
    poller = await _make_user(db_session, name="aur-pub-poller")
    never_polled = await _make_user(db_session, name="aur-pub-silent")
    await db_session.commit()
    order = await _make_order(db_session, publisher.id)

    _set_online_users(monkeypatch)  # 服务端当前认为无人 WS 在线
    sent = _record_ws_sends(monkeypatch)
    await active_user_registry.touch(poller.id)

    count = await notify_boosters_new_order(
        db_session, order=order, exclude_user_id=publisher.id
    )

    assert count == 1
    # 通知由后台独立 session 已提交；先结束本会话读事务再查询。
    await db_session.commit()
    notified = await _notified_user_ids(db_session, order.id)
    assert notified == [poller.id]
    assert never_polled.id not in notified
    assert [item["user_id"] for item in sent] == [poller.id]


async def test_designated_order_ignores_recent_pollers(db_session, monkeypatch):
    """指定单仍只追目标，即使另一个用户刚轮询过通知。"""
    publisher = await _make_user(
        db_session, name="aur-desig-publisher", role=UserRole.ADMIN
    )
    target = await _make_user(db_session, name="aur-desig-target")
    poller = await _make_user(db_session, name="aur-desig-poller")
    await db_session.commit()
    order = await _make_order(
        db_session, publisher.id, designated_booster_id=target.id
    )
    await active_user_registry.touch(poller.id)

    registry_lookups: list[float] = []

    async def _record_registry_lookup(within_seconds: float) -> set[int]:
        registry_lookups.append(within_seconds)
        return {poller.id}

    monkeypatch.setattr(
        active_user_registry, "recent_user_ids", _record_registry_lookup
    )
    _set_online_users(monkeypatch, poller.id)
    sent = _record_ws_sends(monkeypatch)

    count = await notify_boosters_new_order(db_session, order=order)

    assert count == 1
    assert registry_lookups == []
    await db_session.commit()
    notified = await _notified_user_ids(db_session, order.id)
    assert notified == [target.id]
    assert [item["user_id"] for item in sent] == [target.id]


async def test_registry_failure_falls_back_to_ws_online_users(
    db_session, monkeypatch
):
    """registry 查询异常只做降级：仍按 WS 在线集合发单，不抛回调用方。"""
    publisher = await _make_user(
        db_session, name="aur-fail-publisher", role=UserRole.ADMIN
    )
    online_user = await _make_user(db_session, name="aur-fail-online")
    poller = await _make_user(db_session, name="aur-fail-poller")
    await db_session.commit()
    order = await _make_order(db_session, publisher.id)
    await active_user_registry.touch(poller.id)

    async def _boom(within_seconds: float) -> set[int]:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(active_user_registry, "recent_user_ids", _boom)
    _set_online_users(monkeypatch, online_user.id)
    _record_ws_sends(monkeypatch)

    count = await notify_boosters_new_order(
        db_session, order=order, exclude_user_id=publisher.id
    )

    assert count == 1
    await db_session.commit()
    notified = await _notified_user_ids(db_session, order.id)
    assert notified == [online_user.id]
    assert poller.id not in notified


# ---------------------------------------------------------------------------
# 通知端点登记当前用户
# ---------------------------------------------------------------------------


async def test_list_notifications_touches_current_user(client, registered_user):
    user_id = registered_user["user"]["id"]
    active_user_registry.reset()

    response = await client.get(
        "/notifications", headers=auth_header(registered_user)
    )

    assert response.status_code == 200, response.text
    assert await _recent(user_id)


async def test_unread_count_touches_current_user(client, registered_user):
    user_id = registered_user["user"]["id"]
    active_user_registry.reset()

    response = await client.get(
        "/notifications/unread-count", headers=auth_header(registered_user)
    )

    assert response.status_code == 200, response.text
    assert await _recent(user_id)
