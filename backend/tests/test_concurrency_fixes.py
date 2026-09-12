"""并发治理测试：连接管理器多连接并存、事务后广播时序、大厅轮询瘦身响应。

对应 2026-09-12 的卡顿/掉线整改：
- 同一用户多标签页连接并存互不挤掉，慢客户端超时被摘除且不拖累别人；
- WebSocket 扇出登记到事务 commit 之后执行，回滚（409/422）时不广播；
- ``GET /orders/?slim=1`` 剥掉列表卡片用不到的大字段，详情接口保持全量。
"""

import asyncio
import importlib
import time

connection_manager_module = importlib.import_module("app.services.connection_manager")

from httpx import AsyncClient
from sqlalchemy import select

from app.models.notification import Notification
from app.services.connection_manager import connection_manager
from tests.conftest import auth_header

MONKEY_TIMEOUT = 0.05


class FakeWebSocket:
    """最小 WebSocket 替身：记录 send/close 行为，可注入延迟与故障。"""

    def __init__(self, *, delay: float = 0.0, broken: bool = False):
        self.sent: list[dict] = []
        self.closed = False
        self.delay = delay
        self.broken = broken

    async def send_json(self, data: dict) -> None:
        if self.broken:
            raise RuntimeError("broken socket")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# ConnectionManager：多连接并存 / 超时摘除
# ---------------------------------------------------------------------------


async def test_manager_keeps_multiple_connections_per_user(monkeypatch):
    """同一用户两条连接（多标签页）都收到消息，且互不挤掉。"""
    monkeypatch.setattr(
        connection_manager_module, "_SEND_TIMEOUT_SECONDS", MONKEY_TIMEOUT
    )
    ws_a, ws_b = FakeWebSocket(), FakeWebSocket()
    await connection_manager.connect(9001, ws_a)
    await connection_manager.connect(9001, ws_b)
    try:
        await connection_manager.send_to_user(9001, {"event": "ping"})
        assert len(ws_a.sent) == 1
        assert len(ws_b.sent) == 1
        assert not ws_a.closed and not ws_b.closed
    finally:
        await connection_manager.disconnect(9001, ws_a)
        await connection_manager.disconnect(9001, ws_b)


async def test_manager_prunes_slow_socket_without_blocking_others(monkeypatch):
    """慢客户端超时被摘除关闭，同用户其他连接正常收到消息。"""
    monkeypatch.setattr(
        connection_manager_module, "_SEND_TIMEOUT_SECONDS", MONKEY_TIMEOUT
    )
    slow = FakeWebSocket(delay=10.0)
    fast = FakeWebSocket()
    await connection_manager.connect(9002, slow)
    await connection_manager.connect(9002, fast)
    try:
        started = time.monotonic()
        await connection_manager.send_to_user(9002, {"event": "ping"})
        elapsed = time.monotonic() - started
        # 等待的是超时（0.05s）而不是慢客户端的 10s
        assert elapsed < 5, f"send blocked {elapsed:.2f}s on slow socket"
        assert slow.closed
        assert len(fast.sent) == 1 and not fast.closed
    finally:
        await connection_manager.disconnect(9002, slow)
        await connection_manager.disconnect(9002, fast)


async def test_manager_send_to_missing_user_is_noop(monkeypatch):
    monkeypatch.setattr(
        connection_manager_module, "_SEND_TIMEOUT_SECONDS", MONKEY_TIMEOUT
    )
    await connection_manager.send_to_user(424242, {"event": "ping"})


# ---------------------------------------------------------------------------
# 事务后广播：commit 成功才发，回滚不发
# ---------------------------------------------------------------------------


async def _record_ws_sends(monkeypatch) -> list[dict]:
    recorded: list[dict] = []

    async def _record(user_id: int, notification: dict) -> None:
        recorded.append({"user_id": user_id, **notification})

    monkeypatch.setattr(connection_manager, "send_notification", _record)
    return recorded


async def test_publish_broadcasts_only_after_commit(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session, monkeypatch
):
    """发单成功：通知落库 + WS 广播在响应返回时已完成（依赖 teardown 已 drain）。"""
    recorded = await _record_ws_sends(monkeypatch)

    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201, resp.text

    # WS 广播发生在 commit 之后：响应返回时已送达记录器
    assert len(recorded) == 1
    assert recorded[0]["user_id"] == booster_user["user"]["id"]

    rows = (
        await db_session.execute(
            select(Notification).where(Notification.ref_id == resp.json()["id"])
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_failed_publish_does_not_broadcast(
    client: AsyncClient, admin_user: dict, monkeypatch
):
    """发布失败（422）事务回滚：不得产生任何 WS 广播。"""
    recorded = await _record_ws_sends(monkeypatch)

    resp = await client.post(
        "/orders/create",
        json={"game_name": "王者荣耀", "current_rank": "钻石", "target_rank": "王者"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 422
    assert recorded == []


# ---------------------------------------------------------------------------
# 大厅瘦身响应
# ---------------------------------------------------------------------------


async def test_slim_list_strips_heavy_fields_but_detail_keeps_full(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """slim=1 的列表剥掉大字段并用 description_raw 补 intro；详情保持全量。"""
    create = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "description_raw": "帮我从钻石打上王者，需要每天晚上在线，工期一周以内",
        },
        headers=auth_header(admin_user),
    )
    assert create.status_code == 201, create.text
    order_id = create.json()["id"]

    slim_list = (
        await client.get(
            "/orders/", params={"slim": True}, headers=auth_header(booster_user)
        )
    ).json()
    slim_item = next(item for item in slim_list["items"] if item["id"] == order_id)
    assert slim_item["description_raw"] is None
    assert slim_item["description"] is None
    assert slim_item["game_account"] is None
    # 摘要兜底：intro 补上 description_raw 前 40 字
    assert slim_item["intro"].startswith("帮我从钻石打上王者")
    # 卡片仍需要的字段保留
    assert slim_item["price"] == "100.00"
    assert slim_item["game_name"] == "王者荣耀"

    full_list = (
        await client.get("/orders/", headers=auth_header(booster_user))
    ).json()
    full_item = next(item for item in full_list["items"] if item["id"] == order_id)
    assert full_item["description_raw"].startswith("帮我从钻石打上王者")

    detail = (
        await client.get(f"/orders/{order_id}", headers=auth_header(booster_user))
    ).json()
    assert detail["description_raw"].startswith("帮我从钻石打上王者")
