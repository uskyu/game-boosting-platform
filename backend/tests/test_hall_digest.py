"""大厅对账摘要（GET /orders/hall-digest）测试。

背景：线上单 worker 被大厅全量轮询打满，新增轻量摘要端点让客户端断线兜底
轮询先拉摘要、无变化则跳过全量列表。核心契约：
- 摘要过滤口径与列表 booster_scope 完全一致（非管理员只算可接/自己相关的单，
  管理员不加过滤）；
- 任何会改变「可见集合或其内容」的操作都必须让摘要变化，否则客户端会漏刷新；
- 与查看者无关（不可见）的订单变化不得影响其摘要；
- 列表响应携带同一摘要基线（hall_count / hall_max_updated_at /
  hall_earliest_deadline），与摘要端点返回值一致。
"""

from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import update

from app.models.order import Order
from tests.conftest import auth_header

DIGEST = "/orders/hall-digest"


async def _digest(client: AsyncClient, user: dict) -> dict:
    resp = await client.get(DIGEST, headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _make_order(client: AsyncClient, user: dict, **overrides) -> dict:
    body = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": "100.00",
        **overrides,
    }
    resp = await client.post("/orders/create", json=body, headers=auth_header(user))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: float = 500.0):
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "hall digest test"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


async def _age_order(db_session, order_id: int, when: datetime = datetime(2020, 1, 1)):
    """把 updated_at 拨回过去：让「操作触发 updated_at 变化」可被稳定观察
    （MySQL DATETIME 秒级精度，同一秒内的新旧值可能相同）。"""
    await db_session.execute(
        update(Order).where(Order.id == order_id).values(updated_at=when)
    )
    await db_session.commit()


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 基础契约
# ---------------------------------------------------------------------------


async def test_hall_digest_requires_auth_and_returns_shape(
    client: AsyncClient, booster_user: dict
):
    """未登录 401/403；空库返回 count=0 且两个时间字段为 null。"""
    unauthenticated = await client.get(DIGEST)
    assert unauthenticated.status_code in (401, 403)

    digest = await _digest(client, booster_user)
    assert digest == {
        "count": 0,
        "max_updated_at": None,
        "earliest_deadline": None,
        # 顶部统计条的全量数字（2026-10-19 新增）：空库全为 0
        "pending_count": 0,
        "active_count": 0,
        "pending_review_count": 0,
        "completed_today": 0,
    }


async def test_new_claimable_order_changes_count(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """新发布一个可接订单：查看者 count +1，max_updated_at 从 None 变为具体时间。"""
    before = await _digest(client, booster_user)
    assert before["max_updated_at"] is None

    await _make_order(client, admin_user)

    after = await _digest(client, booster_user)
    assert after["count"] == before["count"] + 1
    assert after["max_updated_at"] is not None
    assert after["earliest_deadline"] is None  # 未设截止时间


async def test_filled_order_changes_count(
    client: AsyncClient, admin_user: dict, booster_user: dict, registered_user: dict
):
    """订单被接满（claimed_count == max_claims）后，未报名的查看者不再可见。"""
    order = await _make_order(client, admin_user, max_claims=1)
    before = await _digest(client, booster_user)
    assert before["count"] == 1

    resp = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(registered_user)
    )
    assert resp.status_code == 200, resp.text

    after = await _digest(client, booster_user)
    assert after["count"] == before["count"] - 1


async def test_paused_order_changes_count(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """claim_status 改 PAUSED：暂停抢单后大厅不再可见。"""
    order = await _make_order(client, admin_user)
    assert (await _digest(client, booster_user))["count"] == 1

    resp = await client.put(
        f"/orders/{order['id']}/claim-control",
        json={"action": "pause"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    assert (await _digest(client, booster_user))["count"] == 0


async def test_archived_order_changes_count(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """订单归档：从大厅可见集合中移除。"""
    order = await _make_order(client, admin_user)
    assert (await _digest(client, booster_user))["count"] == 1

    resp = await client.put(
        f"/orders/{order['id']}/claim-control",
        json={"action": "archive"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    assert (await _digest(client, booster_user))["count"] == 0


async def test_deadline_in_past_changes_count_and_earliest_deadline(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """截止时间设为过去：对其他查看者 count 减少；发布人自己仍可见且
    earliest_deadline 变为该过去时间（摘要必须能反映这种「可见但已过期」的单）。"""
    order = await _make_order(client, admin_user)
    assert (await _digest(client, booster_user))["count"] == 1

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
    resp = await client.put(
        f"/orders/{order['id']}",
        json={"deadline": past.isoformat()},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    after_viewer = await _digest(client, booster_user)
    assert after_viewer["count"] == 0
    assert after_viewer["earliest_deadline"] is None

    after_owner = await _digest(client, admin_user)
    assert after_owner["count"] == 1  # 自己的单无论状态如何都可见
    assert _parse(after_owner["earliest_deadline"]) == past


async def test_viewer_own_order_status_change_updates_max_updated_at(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """查看者自己的订单状态变化：count 不变（自己的单始终可见），
    max_updated_at 必须跟着变。"""
    await _fund(client, admin_user, booster_user)
    order = await _make_order(client, booster_user)
    await _age_order(db_session, order["id"])

    before = await _digest(client, booster_user)
    assert before["count"] == 1
    assert _parse(before["max_updated_at"]).year == 2020

    resp = await client.put(
        f"/orders/{order['id']}/cancel", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text

    after = await _digest(client, booster_user)
    assert after["count"] == before["count"]
    assert _parse(after["max_updated_at"]) > _parse(before["max_updated_at"])


async def test_invisible_order_changes_do_not_affect_viewer(
    client: AsyncClient, admin_user: dict, booster_user: dict, registered_user: dict, db_session
):
    """A 发的单只在「B 也可见」时影响 B 的摘要：可接单时 +1，暂停（不可见）后
    继续修改（含 updated_at 变化）不得再影响 B。"""
    await _fund(client, admin_user, registered_user)
    order = await _make_order(client, registered_user)
    assert (await _digest(client, booster_user))["count"] == 1  # 可接=可见=有影响

    resp = await client.put(
        f"/orders/{order['id']}/claim-control",
        json={"action": "pause"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    baseline = await _digest(client, booster_user)
    # 对账基线仍是三个字段；统计条四个数字（2026-10-19 新增）此时应全为 0。
    # 后者也必须在「不可见订单被改」时保持不变，所以一起进基线比对。
    assert baseline["count"] == 0
    assert baseline["max_updated_at"] is None
    assert baseline["earliest_deadline"] is None
    assert baseline["pending_count"] == 0
    assert baseline["active_count"] == 0
    assert baseline["pending_review_count"] == 0
    assert baseline["completed_today"] == 0

    # 对该查看者不可见的订单继续被修改（标题/截止都改，updated_at 也被 SQLAlchemy 刷新）
    await db_session.execute(
        update(Order)
        .where(Order.id == order["id"])
        .values(title="改个标题", deadline=datetime(2020, 1, 1))
    )
    await db_session.commit()

    assert await _digest(client, booster_user) == baseline


async def test_list_response_carries_same_digest(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """列表响应的 hall_* 三个字段与 hall-digest 端点返回值一致。"""
    await _make_order(client, admin_user)
    order = await _make_order(client, admin_user)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    resp = await client.put(
        f"/orders/{order['id']}",
        json={"deadline": future.isoformat()},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    digest = await _digest(client, booster_user)
    assert digest["count"] == 2
    assert _parse(digest["earliest_deadline"]) == future

    listing = await client.get("/orders/", headers=auth_header(booster_user))
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["hall_count"] == digest["count"]
    assert body["hall_max_updated_at"] == digest["max_updated_at"]
    assert body["hall_earliest_deadline"] == digest["earliest_deadline"]
    # 列表原有字段语义不受影响
    assert body["total"] == digest["count"]
    assert body["page"] == 1 and body["page_size"] == 20


async def test_admin_digest_has_no_scope_filter(
    client: AsyncClient, admin_user: dict, booster_user: dict, registered_user: dict
):
    """管理员视角不加过滤：不同发布者的单全部计入（含普通查看者看不到的暂停单）。"""
    await _fund(client, admin_user, booster_user)
    await _fund(client, admin_user, registered_user)

    order_admin = await _make_order(client, admin_user)
    await _make_order(client, booster_user)
    await _make_order(client, registered_user)

    resp = await client.put(
        f"/orders/{order_admin['id']}/claim-control",
        json={"action": "pause"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    admin_digest = await _digest(client, admin_user)
    booster_digest = await _digest(client, booster_user)
    assert admin_digest["count"] == 3  # 全部订单，含暂停单
    # 打手 B 只看到：自己的单 + 他人可接的单（暂停的管理员单不可见）
    assert booster_digest["count"] == 2
    assert admin_digest["count"] > booster_digest["count"]
