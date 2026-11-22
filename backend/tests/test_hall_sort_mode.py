"""大厅排序模式（site_settings.hall_sort_mode）测试。

覆盖角色与端点差异：
- 默认 NEWEST（发布时间倒序），与旧行为一致；
- 管理员可通过 PUT /admin/site/settings 切换 OLDEST，公开 GET /site/settings 同值；
- 大厅列表（含管理员浏览大厅）跟随设置；「我的派单」与后台派单管理始终倒序；
- hall-digest 与大厅列表响应都带 hall_sort_mode，供客户端并入对账键；
- 非法值被 422 拒绝；保存后进程内缓存立即失效（不必等 15 秒）；
- 指定派单的可见性规则不受排序模式影响。
"""

import asyncio
from datetime import datetime

from httpx import AsyncClient
from sqlalchemy import update

from app.models.order import Order
from tests.conftest import auth_header

HALL = "/orders/"
DIGEST = "/orders/hall-digest"
ADMIN_SETTINGS = "/admin/site/settings"
PUBLIC_SETTINGS = "/site/settings"
ADMIN_ORDERS = "/admin/orders"

# 三个明确不同的发布时间（MySQL DATETIME 秒级精度，避免同秒不确定）
_T1 = datetime(2021, 1, 1, 0, 0, 0)
_T2 = datetime(2021, 1, 2, 0, 0, 0)
_T3 = datetime(2021, 1, 3, 0, 0, 0)


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


async def _set_created_at(db_session, order_id: int, when: datetime) -> None:
    await db_session.execute(
        update(Order).where(Order.id == order_id).values(created_at=when)
    )
    await db_session.commit()


async def _seed_orders_with_times(
    client: AsyncClient, publisher: dict, db_session, *, prefix: str = "排序"
) -> dict[str, int]:
    """建 3 单并固定 created_at 为 T1 < T2 < T3；返回 early/middle/late 三个 id。"""
    created = [
        await _make_order(client, publisher, title=f"{prefix}-{index}")
        for index in range(3)
    ]
    await _set_created_at(db_session, created[0]["id"], _T1)
    await _set_created_at(db_session, created[1]["id"], _T2)
    await _set_created_at(db_session, created[2]["id"], _T3)
    return {
        "early": created[0]["id"],
        "middle": created[1]["id"],
        "late": created[2]["id"],
    }


async def _set_sort_mode(client: AsyncClient, admin_user: dict, mode: str) -> dict:
    resp = await client.put(
        ADMIN_SETTINGS,
        json={"site_name": "游戏服务平台", "hall_sort_mode": mode},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _hall_ids(client: AsyncClient, user: dict, **params) -> list[int]:
    resp = await client.get(HALL, headers=auth_header(user), params={"page_size": 100, **params})
    assert resp.status_code == 200, resp.text
    return [item["id"] for item in resp.json()["items"]]


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount=500) -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "hall sort mode test"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


# ---------------------------------------------------------------------------
# 默认值 / 设置持久化
# ---------------------------------------------------------------------------


async def test_default_sort_mode_is_newest(client: AsyncClient):
    """未配置过时公开设置返回 NEWEST（列 server_default 保证升级即为原行为）。"""
    assert (await client.get(PUBLIC_SETTINGS)).json()["hall_sort_mode"] == "NEWEST"


async def test_hall_default_lists_newest_first(
    client: AsyncClient, db_session, admin_user: dict, booster_user: dict
):
    """默认模式：大厅按发布时间倒序，响应携带 hall_sort_mode=NEWEST。"""
    ids = await _seed_orders_with_times(client, admin_user, db_session)
    listing = await client.get(HALL, headers=auth_header(booster_user), params={"page_size": 100})
    assert listing.status_code == 200, listing.text
    body = listing.json()
    listed = [item["id"] for item in body["items"]]
    assert listed == [ids["late"], ids["middle"], ids["early"]]
    assert body["hall_sort_mode"] == "NEWEST"


async def test_admin_can_persist_oldest_and_public_settings_reflect_it(
    client: AsyncClient, admin_user: dict, db_session
):
    """PUT 保存 OLDEST 后公开 GET 同值；不传该字段时保持不变。"""
    body = await _set_sort_mode(client, admin_user, "OLDEST")
    assert body["hall_sort_mode"] == "OLDEST"
    assert (await client.get(PUBLIC_SETTINGS)).json()["hall_sort_mode"] == "OLDEST"

    # 只改别的设置（不传 hall_sort_mode）：None = 不改动
    resp = await client.put(
        ADMIN_SETTINGS,
        json={"site_name": "游戏服务平台改名"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["hall_sort_mode"] == "OLDEST"
    assert (await client.get(PUBLIC_SETTINGS)).json()["hall_sort_mode"] == "OLDEST"


async def test_invalid_sort_mode_is_rejected(
    client: AsyncClient, admin_user: dict
):
    """非法取值由 Pydantic Literal 拒绝（422），不会落库。"""
    resp = await client.put(
        ADMIN_SETTINGS,
        json={"site_name": "游戏服务平台", "hall_sort_mode": "BOGUS"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 422, resp.text
    assert (await client.get(PUBLIC_SETTINGS)).json()["hall_sort_mode"] == "NEWEST"


# ---------------------------------------------------------------------------
# 大厅排序方向
# ---------------------------------------------------------------------------


async def test_hall_lists_oldest_first_in_oldest_mode(
    client: AsyncClient, db_session, admin_user: dict, booster_user: dict
):
    """OLDEST：大厅按发布时间正序；响应仍带 hall_sort_mode=OLDEST。"""
    await _set_sort_mode(client, admin_user, "OLDEST")
    ids = await _seed_orders_with_times(client, admin_user, db_session)

    listing = await client.get(HALL, headers=auth_header(booster_user), params={"page_size": 100})
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert [item["id"] for item in body["items"]] == [ids["early"], ids["middle"], ids["late"]]
    assert body["hall_sort_mode"] == "OLDEST"


async def test_admin_browsing_hall_follows_sort_mode(
    client: AsyncClient, db_session, admin_user: dict
):
    """管理员浏览公开大厅也跟随设置（同一大厅对所有人顺序一致）。"""
    await _set_sort_mode(client, admin_user, "OLDEST")
    ids = await _seed_orders_with_times(client, admin_user, db_session)

    listing = await client.get(HALL, headers=auth_header(admin_user), params={"page_size": 100})
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert [item["id"] for item in body["items"]] == [ids["early"], ids["middle"], ids["late"]]
    assert body["hall_sort_mode"] == "OLDEST"


async def test_mine_published_keeps_newest_first_in_oldest_mode(
    client: AsyncClient, db_session, admin_user: dict, registered_user: dict
):
    """「我的派单」不受大厅设置影响：OLDEST 模式下仍按发布时间倒序。"""
    await _fund(client, admin_user, registered_user)
    await _set_sort_mode(client, admin_user, "OLDEST")
    ids = await _seed_orders_with_times(client, registered_user, db_session, prefix="我的派单")

    listing = await client.get(
        HALL,
        headers=auth_header(registered_user),
        params={"page_size": 100, "mine_published": "true"},
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert [item["id"] for item in body["items"]] == [ids["late"], ids["middle"], ids["early"]]
    # 我的派单不是大厅口径：排序模式与摘要基线都不下发
    assert body["hall_sort_mode"] is None
    assert body["hall_count"] is None


async def test_admin_dispatch_list_ignores_sort_mode(
    client: AsyncClient, db_session, admin_user: dict
):
    """后台派单管理列表始终倒序（admin.py 用 list_orders 默认参数）。"""
    await _set_sort_mode(client, admin_user, "OLDEST")
    ids = await _seed_orders_with_times(client, admin_user, db_session)

    resp = await client.get(ADMIN_ORDERS, headers=auth_header(admin_user), params={"page_size": 100})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["id"] for item in body["items"]] == [ids["late"], ids["middle"], ids["early"]]
    assert body["hall_sort_mode"] is None


# ---------------------------------------------------------------------------
# 摘要 / 对账
# ---------------------------------------------------------------------------


async def test_hall_digest_includes_sort_mode(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """摘要包含排序模式：切换后客户端对账键必然变化。"""
    digest = await client.get(DIGEST, headers=auth_header(booster_user))
    assert digest.status_code == 200, digest.text
    assert digest.json()["hall_sort_mode"] == "NEWEST"

    await _set_sort_mode(client, admin_user, "OLDEST")
    digest = await client.get(DIGEST, headers=auth_header(booster_user))
    assert digest.status_code == 200, digest.text
    assert digest.json()["hall_sort_mode"] == "OLDEST"

    # include_stats=false 的轻量轮询同样带排序模式
    digest = await client.get(
        DIGEST, headers=auth_header(booster_user), params={"include_stats": "false"}
    )
    assert digest.status_code == 200, digest.text
    assert digest.json()["hall_sort_mode"] == "OLDEST"


async def test_cache_invalidated_immediately_after_admin_save(
    client: AsyncClient, db_session, admin_user: dict, booster_user: dict
):
    """先预热缓存（NEWEST），管理员保存 OLDEST 后立刻请求必须已是 OLDEST。"""
    ids = await _seed_orders_with_times(client, admin_user, db_session)
    # 预热：第一次大厅列表把 NEWEST 写入进程内缓存
    assert await _hall_ids(client, booster_user) == [ids["late"], ids["middle"], ids["early"]]

    await _set_sort_mode(client, admin_user, "OLDEST")

    # 保存后的第一次请求：缓存已失效 → 立即读到 OLDEST（不是 15 秒后才生效）
    assert await _hall_ids(client, booster_user) == [ids["early"], ids["middle"], ids["late"]]


# ---------------------------------------------------------------------------
# WebSocket 广播
# ---------------------------------------------------------------------------


async def test_broadcast_hall_sort_changed_runs_after_commit(monkeypatch):
    """hall_sort_changed 事件登记后不会在事务提交前发送（与订单状态事件同机制）。"""
    import app.api.order_events as order_events_module
    from app.core.post_commit import drain_registry, start_registry, stop_registry

    recorded: list[dict] = []

    async def _record(payload: dict) -> None:
        recorded.append(payload)

    monkeypatch.setattr(
        order_events_module.get_connection_manager(), "broadcast", _record
    )
    token = start_registry()
    try:
        await order_events_module.broadcast_hall_sort_changed(sort_mode="OLDEST")
        assert recorded == []
        await drain_registry()
        await asyncio.sleep(0)
        assert recorded == [
            {"event": "hall_sort_changed", "data": {"sort_mode": "OLDEST"}}
        ]
    finally:
        stop_registry(token)


async def test_admin_save_broadcasts_only_when_mode_changes(
    client: AsyncClient, admin_user: dict, monkeypatch
):
    """保存设置时：排序模式真正变化才广播，重复保存同一值不重复广播。"""
    import app.api.endpoints.admin_site as admin_site_module

    recorded: list[str] = []

    async def _record(*, sort_mode: str) -> None:
        recorded.append(sort_mode)

    monkeypatch.setattr(admin_site_module, "broadcast_hall_sort_changed", _record)

    await _set_sort_mode(client, admin_user, "OLDEST")
    assert recorded == ["OLDEST"]

    # 再保存一次同样的值：没有变化，不应再广播
    await _set_sort_mode(client, admin_user, "OLDEST")
    assert recorded == ["OLDEST"]

    # 切回 NEWEST：又变化了，需要广播
    await _set_sort_mode(client, admin_user, "NEWEST")
    assert recorded == ["OLDEST", "NEWEST"]


# ---------------------------------------------------------------------------
# 可见性不受影响
# ---------------------------------------------------------------------------


async def test_designated_order_visibility_unaffected_by_sort_mode(

    client: AsyncClient,
    db_session,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    make_captcha,
):
    """排列方向变了，指定派单的可见范围不变：目标可见、第三人不可见。"""
    await _fund(client, admin_user, registered_user)
    await _set_sort_mode(client, admin_user, "OLDEST")

    target_id = booster_user["user"]["id"]
    designated = await _make_order(
        client,
        registered_user,
        title="指定单-排序模式",
        designated_booster_id=target_id,
        max_claims=1,
    )
    public = await _make_order(client, registered_user, title="公开单-排序模式")
    await _set_created_at(db_session, designated["id"], _T1)
    await _set_created_at(db_session, public["id"], _T2)

    # 指定人：两单都可见，且按 OLDEST 正序
    target_ids = await _hall_ids(client, booster_user)
    assert target_ids == [designated["id"], public["id"]]

    # 第三人：只能看到公开单
    resp = await client.post(
        "/auth/register",
        json={
            "email": "hall-sort-third@example.com",
            "username": "HallSortThird",
            "password": "Passw0rd123",
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    third = resp.json()
    third_ids = await _hall_ids(client, third)
    assert designated["id"] not in third_ids
    assert third_ids == [public["id"]]
