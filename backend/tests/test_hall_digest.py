"""大厅对账摘要（GET /orders/hall-digest）测试。

背景：线上单 worker 被大厅全量轮询打满，新增轻量摘要端点让客户端断线兜底
轮询先拉摘要、无变化则跳过全量列表。核心契约：
- 摘要过滤口径与列表 ``public_hall`` 口径完全一致（``build_public_hall_scope``：
  只算可抢订单，**所有角色包括管理员**都一样；管理员的全量订单视图在
  ``GET /admin/orders``）；
- 任何会改变「大厅可见集合或其内容」的操作都必须让摘要变化，否则客户端会漏刷新；
- 与查看者无关（不可见）的订单变化不得影响其摘要；
- 列表响应携带同一摘要基线（hall_count / hall_max_updated_at /
  hall_earliest_deadline），与摘要端点返回值一致。

2026-11-22 线上事故后口径收窄：摘要此前含「自己发布/已接/已报名」的历史单
（管理员完全不过滤），count 远大于大厅实际能渲染的条目，OLDEST 排序下发布者
的旧 COMPLETED 单填满第一页、前端过滤后大厅为空。
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
        # 默认调用（不传 include_stats）与旧行为一致：统计参与计算
        "stats_included": True,
        # 大厅排序模式（2026-11-22 新增）：默认最新在前
        "hall_sort_mode": "NEWEST",
    }


async def test_hall_digest_include_stats_false_skips_stats(
    client: AsyncClient, admin_user: dict, booster_user: dict, monkeypatch
):
    """include_stats=false：跳过 hall_stats，四个统计字段仍为数字 0（不为 null），
    并返回 stats_included=false，让前端保留上一次有效统计。"""
    from app.services.order_service import OrderService

    calls = {"count": 0}

    async def _must_not_be_called(self, user):
        calls["count"] += 1
        raise AssertionError("include_stats=false 时不应调用 hall_stats")

    monkeypatch.setattr(OrderService, "hall_stats", _must_not_be_called)

    # 有可接订单时摘要 count 正常，但统计必须被跳过（pending_count 不能是 1）
    await _make_order(client, admin_user)

    resp = await client.get(
        DIGEST,
        params={"include_stats": "false"},
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert calls["count"] == 0
    assert body["stats_included"] is False
    assert body["count"] == 1
    assert body["max_updated_at"] is not None
    assert body["earliest_deadline"] is None
    for field in ("pending_count", "active_count", "pending_review_count", "completed_today"):
        assert body[field] == 0
        assert body[field] is not None


async def test_hall_digest_include_stats_true_calls_stats(
    client: AsyncClient, booster_user: dict, monkeypatch
):
    """include_stats=true（显式）与默认调用一样计算并返回统计，stats_included=true。"""
    from app.services.order_service import OrderService

    calls = {"count": 0}
    original = OrderService.hall_stats

    async def _record(self, user):
        calls["count"] += 1
        return await original(self, user)

    monkeypatch.setattr(OrderService, "hall_stats", _record)

    resp = await client.get(
        DIGEST,
        params={"include_stats": "true"},
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert calls["count"] == 1
    assert body["stats_included"] is True
    assert {
        "pending_count",
        "active_count",
        "pending_review_count",
        "completed_today",
    } <= body.keys()


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
    """截止时间设为过去：该单不再可抢，对**所有角色**（含发布人自己、管理员）
    都退出大厅口径——摘要 count 归零、earliest_deadline 归 None。

    2026-11-22：大厅口径收窄为 claimable-only 后，「自己的单无论状态如何都
    可见」不再成立；过期单属于个人历史，走「我的派单」而非大厅摘要。
    """
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
    assert after_owner["count"] == 0  # 已过期 → 不再可抢，不进大厅摘要
    assert after_owner["earliest_deadline"] is None


async def test_viewer_own_order_status_change_updates_max_updated_at(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """查看者自己发布的可抢订单被取消：它退出大厅口径，摘要 max_updated_at
    回落为 None（大厅里已没有可抢订单）。"""
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

    # 取消后不再是可抢订单 → 大厅口径为空（历史单走「我的派单」）
    after = await _digest(client, booster_user)
    assert after["count"] == 0
    assert after["max_updated_at"] is None
    assert after["earliest_deadline"] is None


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


async def test_admin_digest_is_claimable_only_too(
    client: AsyncClient, admin_user: dict, booster_user: dict, registered_user: dict
):
    """管理员的大厅摘要同样只算可抢订单（不再「不加过滤」）。

    2026-11-22：大厅口径对**所有角色**收窄为 claimable-only。暂停单（含管理员
    自己发的）都不可抢，因此不进任何人的大厅摘要；管理员的全量订单视图在
    ``GET /admin/orders``（另有专项用例覆盖）。
    """
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
    # 暂停单不可抢 → 管理员也看不到它；剩下两张他人发的可抢单
    assert admin_digest["count"] == 2
    # 打手 B 自己的单此时已被暂停（不可抢），只剩他人两张可抢单 → 2
    assert booster_digest["count"] == 2
    assert admin_digest["count"] == booster_digest["count"]
