"""指定派单的「新订单发布」通知定向行为。

- 指定单只给 designated_booster_id 对应的目标用户发 DB 通知与 WS 卡片；
- 其他活跃可接单用户一条都不发；
- 公开单仍按原规则全员扇出；
- 目标关闭「新订单」偏好时不发；
- 标题保持「新订单发布」（前端靠标题识别提示音/新单卡片）。
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.api.notification_utils import notify_boosters_new_order
from app.models.notification import Notification, NotificationType, UserPreference
from app.models.order import Order
from app.models.user import User, UserRole
from app.schemas.serializers import serialize_datetime_utc
from app.services.connection_manager import connection_manager

DESIGNATED_CONTENT = "有一张指定给你的新订单「{game_name}」，请前往接单"
PUBLIC_CONTENT = "管理员发布了新订单「{game_name}」，请前往抢单"


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


def _record_ws_sends(monkeypatch) -> list[dict]:
    recorded: list[dict] = []

    async def _record(user_id: int, notification: dict) -> None:
        recorded.append({"user_id": user_id, "payload": notification})

    monkeypatch.setattr(connection_manager, "send_notification", _record)
    return recorded


def _set_online_users(monkeypatch, *user_ids: int) -> None:
    """固定公开单的在线快照；指定单不查在线集合，无需调用。"""
    online_ids = {int(user_id) for user_id in user_ids}

    async def _online_user_ids() -> set[int]:
        return set(online_ids)

    monkeypatch.setattr(connection_manager, "online_user_ids", _online_user_ids)


async def _notifications(db_session, order_id: int) -> list[Notification]:
    result = await db_session.execute(
        select(Notification).where(
            Notification.ref_id == order_id,
            Notification.type == NotificationType.SYSTEM_ANNOUNCEMENT,
        )
    )
    return list(result.scalars().all())


async def test_designated_order_notifies_only_target_with_db_and_ws(
    db_session, monkeypatch
):
    publisher = await _make_user(db_session, name="desig-publisher", role=UserRole.ADMIN)
    target = await _make_user(db_session, name="desig-target")
    other_one = await _make_user(db_session, name="desig-other-1")
    other_two = await _make_user(db_session, name="desig-other-2")
    await db_session.commit()

    order = await _make_order(
        db_session, publisher.id, designated_booster_id=target.id
    )
    order_card = {
        "id": order.id,
        "game_name": order.game_name,
        "current_rank": order.current_rank,
        "target_rank": order.target_rank,
        "price": "300.00",
        "claim_status": "OPEN",
    }
    recorded = _record_ws_sends(monkeypatch)

    count = await notify_boosters_new_order(
        db_session, order=order, order_card=order_card
    )

    assert count == 1
    rows = await _notifications(db_session, order.id)
    assert len(rows) == 1
    assert rows[0].user_id == target.id
    assert rows[0].title == "新订单发布"
    assert rows[0].content == DESIGNATED_CONTENT.format(game_name=order.game_name)
    for user in (other_one, other_two, publisher):
        assert user.id not in {row.user_id for row in rows}

    assert len(recorded) == 1
    assert recorded[0]["user_id"] == target.id
    payload = recorded[0]["payload"]
    assert payload["title"] == "新订单发布"
    assert payload["content"] == DESIGNATED_CONTENT.format(game_name=order.game_name)
    assert payload["link"] == f"/orders/{order.id}"
    # 指定单不查在线集合，收件人按保证金档位（测试库默认关闭 → 0 秒）
    # 生成卡片变体；同档位共享同一份只读 dict。
    assert payload["order_card"] == {
        **order_card,
        "accept_wait_seconds": 0,
        "accept_available_at": serialize_datetime_utc(
            order.created_at.replace(tzinfo=timezone.utc)
        ),
        "accept_window_pending": False,
    }


async def test_public_order_still_broadcasts_to_all_eligible_users(
    db_session, monkeypatch
):
    publisher = await _make_user(db_session, name="pub-publisher", role=UserRole.ADMIN)
    recipients = [
        await _make_user(db_session, name=f"pub-recipient-{index}")
        for index in range(3)
    ]
    inactive = await _make_user(
        db_session, name="pub-inactive", is_active=False
    )
    stopped = await _make_user(db_session, name="pub-stopped", can_accept=False)
    await db_session.commit()

    order = await _make_order(db_session, publisher.id)
    recorded = _record_ws_sends(monkeypatch)
    # 公开单只写在线用户：候选全部在线，验证资格过滤与扇出
    _set_online_users(monkeypatch, *(user.id for user in recipients))

    count = await notify_boosters_new_order(db_session, order=order)

    assert count == 3
    rows = await _notifications(db_session, order.id)
    assert {row.user_id for row in rows} == {user.id for user in recipients}
    for row in rows:
        assert row.title == "新订单发布"
        assert row.content == PUBLIC_CONTENT.format(game_name=order.game_name)
    assert inactive.id not in {row.user_id for row in rows}
    assert stopped.id not in {row.user_id for row in rows}

    assert {item["user_id"] for item in recorded} == {user.id for user in recipients}
    for item in recorded:
        assert item["payload"]["title"] == "新订单发布"
        assert item["payload"]["content"] == PUBLIC_CONTENT.format(
            game_name=order.game_name
        )


async def test_designated_target_with_preference_off_receives_nothing(
    db_session, monkeypatch
):
    publisher = await _make_user(
        db_session, name="desig-pref-publisher", role=UserRole.ADMIN
    )
    target = await _make_user(db_session, name="desig-pref-target")
    other = await _make_user(db_session, name="desig-pref-other")
    db_session.add(
        UserPreference(
            user_id=target.id,
            notification_settings={NotificationType.SYSTEM_ANNOUNCEMENT.value: False},
        )
    )
    await db_session.commit()

    order = await _make_order(
        db_session, publisher.id, designated_booster_id=target.id
    )
    recorded = _record_ws_sends(monkeypatch)

    count = await notify_boosters_new_order(db_session, order=order)

    assert count == 0
    assert await _notifications(db_session, order.id) == []
    assert recorded == []
    assert other.id not in {item["user_id"] for item in recorded}


async def test_designated_target_must_still_pass_existing_filters(
    db_session, monkeypatch
):
    """目标 can_accept=False 时连目标也不发，其他可接单用户更不会收到。"""
    publisher = await _make_user(
        db_session, name="desig-filter-publisher", role=UserRole.ADMIN
    )
    target = await _make_user(
        db_session, name="desig-filter-target", can_accept=False
    )
    other = await _make_user(db_session, name="desig-filter-other")
    await db_session.commit()

    order = await _make_order(
        db_session, publisher.id, designated_booster_id=target.id
    )
    recorded = _record_ws_sends(monkeypatch)

    count = await notify_boosters_new_order(db_session, order=order)

    assert count == 0
    assert await _notifications(db_session, order.id) == []
    assert recorded == []
    assert other.id not in {item["user_id"] for item in recorded}
