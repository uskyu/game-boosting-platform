"""新单广播 payload 携带大厅卡片（order_card）的专项用例。

背景：老板发单时服务端向全员（~600 人）扇出「新订单」WS 通知，客户端收到后
会全量重刷大厅列表，发布瞬间形成全站请求尖峰。止血方案是在广播 payload 里带上
大厅列表卡片的 slim dict，前端据此本地插卡、不再全量拉列表。

测试风格跟随 tests/test_concurrency_fixes.py 的发单广播用例：直接调用
``notify_boosters_new_order`` 并 monkeypatch ``connection_manager.send_notification``
记录扇出内容。请求上下文之外没有 post-commit 注册表，``run_after_commit`` 会立即
执行，因此调用返回后即可断言。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.api.notification_utils import notify_boosters_new_order
from app.models.deposit import DepositSetting, DepositTier
from app.models.order import Order
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from app.schemas.serializers import serialize_datetime_utc
from app.services import deposit_service
from app.services.connection_manager import connection_manager
from tests.conftest import auth_header

ORDER_CARD = {
    "id": 4242,
    "game_name": "三角洲行动",
    "current_rank": "黄金",
    "target_rank": "钻石",
    "price": "300.00",
    "claim_status": "OPEN",
}

# create_order 构建的 WS 基础卡片：等待字段未计算，显式 pending 标记
BASE_PENDING_CARD = {
    **ORDER_CARD,
    "accept_wait_seconds": None,
    "accept_available_at": None,
    "accept_window_pending": True,
}


async def _seed(db_session, recipient_count: int = 3):
    """建 1 个管理员发布者 + N 个活跃打手 + 1 条订单，返回 (order, recipient_ids)。"""
    publisher = User(
        email="publisher@example.com",
        username="Publisher",
        hashed_password="not-used",
        role=UserRole.ADMIN,
    )
    db_session.add(publisher)
    await db_session.flush()

    recipients = [
        User(
            email=f"card-booster-{index}@example.com",
            username=f"CardBooster{index}",
            hashed_password="not-used",
            role=UserRole.BOOSTER,
        )
        for index in range(recipient_count)
    ]
    db_session.add_all(recipients)
    await db_session.flush()

    order = Order(
        user_id=publisher.id,
        game_name="三角洲行动",
        current_rank="黄金",
        target_rank="钻石",
        price=Decimal("300.00"),
    )
    db_session.add(order)
    await db_session.commit()
    return order, {user.id for user in recipients}


def _record_ws_sends(monkeypatch) -> list[dict]:
    recorded: list[dict] = []

    async def _record(user_id: int, notification: dict) -> None:
        recorded.append({"user_id": user_id, "payload": notification})

    monkeypatch.setattr(connection_manager, "send_notification", _record)
    return recorded


async def test_order_card_attached_to_every_broadcast(db_session, monkeypatch):
    """传入 order_card：每条 post-commit 广播 payload 都带同一份卡片。"""
    order, recipient_ids = await _seed(db_session)
    recorded = _record_ws_sends(monkeypatch)

    count = await notify_boosters_new_order(
        db_session, order=order, order_card=ORDER_CARD
    )

    assert count == len(recipient_ids)
    assert {item["user_id"] for item in recorded} == recipient_ids
    for item in recorded:
        payload = item["payload"]
        assert payload["order_card"] == ORDER_CARD
        # 保留原有通知字段
        assert payload["title"] == "新订单发布"
        assert payload["link"] == f"/orders/{order.id}"
    # 全员共享同一个只读引用（不深拷贝）
    assert all(item["payload"]["order_card"] is ORDER_CARD for item in recorded)


async def test_order_card_absent_when_not_provided(db_session, monkeypatch):
    """不传 order_card：payload 中无 order_card 键，行为与历史一致。"""
    order, recipient_ids = await _seed(db_session)
    recorded = _record_ws_sends(monkeypatch)

    count = await notify_boosters_new_order(db_session, order=order)

    assert count == len(recipient_ids)
    assert len(recorded) == len(recipient_ids)
    for item in recorded:
        assert "order_card" not in item["payload"]
        assert item["payload"]["title"] == "新订单发布"
        assert item["payload"]["link"] == f"/orders/{order.id}"


async def test_create_order_broadcasts_pending_base_card(
    client, admin_user, booster_user, monkeypatch
):
    """create_order 构建的 WS 基础卡片必须标记等待时间未计算。"""
    recorded = _record_ws_sends(monkeypatch)
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "title": "WS 卡片 pending 测试",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "boss_contact": "P0001",
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201, resp.text
    assert recorded, "新单广播应至少产生一条 WS payload"
    card = recorded[0]["payload"]["order_card"]
    assert card["accept_window_pending"] is True
    assert card["accept_wait_seconds"] is None
    assert card["accept_available_at"] is None


# ---------------------------------------------------------------------------
# WS 卡片按保证金档位分组（accept_window_pending 兜底 + 在线收件人变体）
# ---------------------------------------------------------------------------


class _DummyWebSocket:
    """connect() 只登记引用；send_notification 已被 patch，不会真的发送。"""


async def _seed_tier_recipients(db_session, recipient_count: int, *, created_at: datetime):
    """建 1 个管理员发布者 + N 个活跃打手 + 1 条订单，返回 (order, recipients)。"""
    publisher = User(
        email=f"tier-publisher-{id(db_session)}@example.com",
        username=f"TierPublisher{id(db_session)}",
        hashed_password="not-used",
        role=UserRole.ADMIN,
    )
    db_session.add(publisher)
    await db_session.flush()

    recipients = [
        User(
            email=f"tier-booster-{index}-{id(db_session)}@example.com",
            username=f"TierBooster{index}{id(db_session)}",
            hashed_password="not-used",
            role=UserRole.BOOSTER,
        )
        for index in range(recipient_count)
    ]
    db_session.add_all(recipients)
    await db_session.flush()

    order = Order(
        user_id=publisher.id,
        game_name="三角洲行动",
        current_rank="黄金",
        target_rank="钻石",
        price=Decimal("300.00"),
        created_at=created_at.replace(tzinfo=None),
    )
    db_session.add(order)
    await db_session.commit()
    return order, recipients


async def _enable_deposit(db_session, *, tiers, balances: dict[int, Decimal]) -> None:
    db_session.add(
        DepositSetting(
            id=1,
            enabled=True,
            return_cooldown_days=7,
            default_compensation=Decimal("20.00"),
            settlement_mode="AFTER_DELIVERY",
            global_booster_quota=5,
        )
    )
    db_session.add_all(
        [
            DepositTier(
                threshold=threshold,
                wait_seconds=wait,
                exempt_compensation=False,
                settle_hours=None,
                enabled=True,
            )
            for threshold, wait in tiers
        ]
    )
    db_session.add_all(
        [
            Wallet(user_id=user_id, deposit_balance=balance)
            for user_id, balance in balances.items()
        ]
    )
    await db_session.commit()


async def _connect_online(user_ids) -> list[tuple[int, _DummyWebSocket]]:
    sockets: list[tuple[int, _DummyWebSocket]] = []
    for user_id in user_ids:
        ws = _DummyWebSocket()
        await connection_manager.connect(user_id, ws)
        sockets.append((user_id, ws))
    return sockets


async def _disconnect_all(sockets) -> None:
    for user_id, ws in sockets:
        await connection_manager.disconnect(user_id, ws)


async def test_ws_cards_grouped_by_tier_for_online_recipients(
    db_session, monkeypatch
):
    """在线收件人按档位拿到带倒计时的卡片变体；同组共享同一 dict 引用。"""
    created = datetime.now(timezone.utc).replace(microsecond=0)
    order, recipients = await _seed_tier_recipients(db_session, 5, created_at=created)
    users = recipients
    # 0 元 → 35s，100 元 → 15s，1000 元 → 5s；users[3] 离线且无钱包
    await _enable_deposit(
        db_session,
        tiers=[(Decimal("0.00"), 35), (Decimal("100.00"), 15), (Decimal("1000.00"), 5)],
        balances={
            users[0].id: Decimal("0.00"),
            users[1].id: Decimal("100.00"),
            users[2].id: Decimal("1000.00"),
            users[4].id: Decimal("0.00"),
        },
    )
    recorded = _record_ws_sends(monkeypatch)
    sockets = await _connect_online([users[0].id, users[1].id, users[2].id, users[4].id])
    try:
        count = await notify_boosters_new_order(
            db_session, order=order, order_card=BASE_PENDING_CARD
        )
    finally:
        await _disconnect_all(sockets)

    assert count == len(recipients)
    by_user = {item["user_id"]: item["payload"]["order_card"] for item in recorded}

    expected = {
        users[0].id: 35,
        users[1].id: 15,
        users[2].id: 5,
        users[4].id: 35,
    }
    for user_id, wait in expected.items():
        card = by_user[user_id]
        assert card["accept_window_pending"] is False
        assert card["accept_wait_seconds"] == wait
        assert card["accept_available_at"] == serialize_datetime_utc(
            created + timedelta(seconds=wait)
        )

    # 同档位组共享同一个 dict 引用（不逐用户深拷贝）
    assert by_user[users[0].id] is by_user[users[4].id]
    assert by_user[users[0].id] is not by_user[users[1].id]

    # 离线用户保留基础 pending 卡片（不会通过 WS 收到，仅保持结构不报错）
    assert by_user[users[3].id] is BASE_PENDING_CARD
    assert by_user[users[3].id]["accept_window_pending"] is True


async def test_ws_cards_zero_wait_when_deposit_disabled(db_session, monkeypatch):
    """保证金关闭：在线收件人统一拿到 wait=0、pending=False 的卡片。"""
    created = datetime.now(timezone.utc).replace(microsecond=0)
    order, recipients = await _seed_tier_recipients(db_session, 2, created_at=created)
    recorded = _record_ws_sends(monkeypatch)
    sockets = await _connect_online([recipient.id for recipient in recipients])
    try:
        await notify_boosters_new_order(
            db_session, order=order, order_card=BASE_PENDING_CARD
        )
    finally:
        await _disconnect_all(sockets)

    cards = [item["payload"]["order_card"] for item in recorded]
    assert all(card["accept_wait_seconds"] == 0 for card in cards)
    assert all(card["accept_window_pending"] is False for card in cards)
    assert all(
        card["accept_available_at"] == serialize_datetime_utc(created) for card in cards
    )
    # 全部命中 0 秒组 → 共享同一个 dict 引用
    assert cards[0] is cards[1]


async def test_ws_cards_fall_back_to_pending_when_bulk_query_fails(
    db_session, monkeypatch
):
    """批量档位查询失败：通知照常发送/落库，卡片降级为基础 pending 卡片。"""
    created = datetime.now(timezone.utc).replace(microsecond=0)
    order, recipients = await _seed_tier_recipients(db_session, 2, created_at=created)
    recorded = _record_ws_sends(monkeypatch)
    sockets = await _connect_online([recipient.id for recipient in recipients])

    async def _boom(*args, **kwargs):
        raise RuntimeError("wallet bulk query failed")

    monkeypatch.setattr(deposit_service, "get_many_tier_wait_seconds", _boom)
    try:
        count = await notify_boosters_new_order(
            db_session, order=order, order_card=BASE_PENDING_CARD
        )
    finally:
        await _disconnect_all(sockets)

    assert count == len(recipients)
    assert len(recorded) == len(recipients)
    for item in recorded:
        assert item["payload"]["order_card"] is BASE_PENDING_CARD
        assert item["payload"]["order_card"]["accept_window_pending"] is True


async def test_get_many_tier_wait_seconds_bulk(db_session):
    """批量档位：一次档位+一次钱包查询，Python 内解析；缺钱包按 0 余额。"""
    users = [
        User(
            email=f"bulk-wait-{index}@example.com",
            username=f"BulkWait{index}",
            hashed_password="not-used",
        )
        for index in range(3)
    ]
    db_session.add_all(users)
    await db_session.flush()
    await _enable_deposit(
        db_session,
        tiers=[(Decimal("0.00"), 35), (Decimal("1000.00"), 5)],
        balances={
            users[0].id: Decimal("1000.00"),
            # users[1] 没有钱包行 → 0 余额
            users[2].id: Decimal("500.00"),
        },
    )

    waits = await deposit_service.get_many_tier_wait_seconds(
        db_session, [user.id for user in users]
    )
    assert waits == {users[0].id: 5, users[1].id: 35, users[2].id: 35}

    # 保证金关闭 → 全 0
    setting = (
        await db_session.execute(select(DepositSetting).where(DepositSetting.id == 1))
    ).scalar_one()
    setting.enabled = False
    await db_session.commit()
    assert await deposit_service.get_many_tier_wait_seconds(
        db_session, [user.id for user in users]
    ) == {users[0].id: 0, users[1].id: 0, users[2].id: 0}
