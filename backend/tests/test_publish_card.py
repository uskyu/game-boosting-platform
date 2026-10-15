"""新单广播 payload 携带大厅卡片（order_card）的专项用例。

背景：老板发单时服务端向全员（~600 人）扇出「新订单」WS 通知，客户端收到后
会全量重刷大厅列表，发布瞬间形成全站请求尖峰。止血方案是在广播 payload 里带上
大厅列表卡片的 slim dict，前端据此本地插卡、不再全量拉列表。

测试风格跟随 tests/test_concurrency_fixes.py 的发单广播用例：直接调用
``notify_boosters_new_order`` 并 monkeypatch ``connection_manager.send_notification``
记录扇出内容。请求上下文之外没有 post-commit 注册表，``run_after_commit`` 会立即
执行，因此调用返回后即可断言。
"""

from decimal import Decimal

from app.api.notification_utils import notify_boosters_new_order
from app.models.order import Order
from app.models.user import User, UserRole
from app.services.connection_manager import connection_manager

ORDER_CARD = {
    "id": 4242,
    "game_name": "三角洲行动",
    "current_rank": "黄金",
    "target_rank": "钻石",
    "price": "300.00",
    "claim_status": "OPEN",
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
