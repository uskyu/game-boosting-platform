"""公开大厅作用域（claimable-only）回归测试。

背景（2026-11-22 线上事故）：``GET /orders/``（不带 ``mine_published``）原先
用 ``build_hall_visibility_scope``，把查看者**自己发布 / 已接 / 已报名**的历史
订单一并带进大厅列表。前端大厅只渲染可抢订单（``isOrderClaimable``），会把
其余条目全部过滤掉；OLDEST 排序上线后，一个拥有数千张历史 COMPLETED 单的
发布者打开大厅，第一页 100 条全是自己的旧单 → 前端过滤后大厅显示为空。

新契约（``build_public_hall_scope``）：
- 公开大厅（含管理员浏览）只下发「现在真的能抢」的订单；
- 「我的派单」（``mine_published=true``）保持原样：自己的单、发布时间倒序；
- 后台派单管理（``GET /admin/orders``）仍是全量订单视图；
- ``hall-digest`` 的 count 与大厅列表 ``total`` 完全一致（客户端据此在
  摘要未变时跳过全量刷新）。
"""

from datetime import datetime, timezone
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import update

from app.models.order import Order, OrderStatus
from tests.conftest import auth_header

HALL = "/orders/"
DIGEST = "/orders/hall-digest"
ADMIN_SETTINGS = "/admin/site/settings"
ADMIN_ORDERS = "/admin/orders"

# 明确不同的发布时间（MySQL DATETIME 秒级精度，避免同秒不确定）
_T_HISTORY = datetime(2021, 9, 1, 0, 0, 0)
_T1 = datetime(2021, 1, 1, 0, 0, 0)
_T2 = datetime(2021, 1, 2, 0, 0, 0)
_T3 = datetime(2021, 1, 3, 0, 0, 0)

HISTORY_COUNT = 120


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


async def _set_sort_mode(client: AsyncClient, admin_user: dict, mode: str) -> None:
    resp = await client.put(
        ADMIN_SETTINGS,
        json={"site_name": "游戏服务平台", "hall_sort_mode": mode},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount=500) -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "hall public scope test"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


async def _insert_history_orders(
    db_session,
    user: dict,
    count: int,
    *,
    status: OrderStatus = OrderStatus.COMPLETED,
    created_at: datetime = _T_HISTORY,
) -> list[int]:
    """直插历史订单（绕过发布 API 的托管/余额校验，模拟线上老数据）。

    状态非 PENDING/LOCKED，因此这些单永远不满足大厅可抢条件；它们此前正是
    靠「发布人自己的单」分支混进大厅列表的。
    """
    orders = [
        Order(
            user_id=user["user"]["id"],
            game_name="王者荣耀",
            current_rank="钻石",
            target_rank="王者",
            price=Decimal("100.00"),
            status=status,
            created_at=created_at,
        )
        for _ in range(count)
    ]
    db_session.add_all(orders)
    await db_session.commit()
    return [order.id for order in orders]


async def _seed_publisher_with_history(
    client: AsyncClient,
    db_session,
    admin_user: dict,
    publisher: dict,
) -> tuple[list[int], list[int]]:
    """发布者名下有 120 张旧 COMPLETED 单 + 2 张可抢 PENDING 单。"""
    await _fund(client, admin_user, publisher, 500)
    history_ids = await _insert_history_orders(db_session, publisher, HISTORY_COUNT)
    claimable = [
        await _make_order(client, publisher, title=f"可抢单-{index}")
        for index in range(2)
    ]
    return history_ids, [order["id"] for order in claimable]


def _is_claimable(item: dict) -> bool:
    """前端 OrderHallView.vue ``isOrderClaimable`` 的等价判定。"""
    if not item or item.get("status") not in ("PENDING", "LOCKED"):
        return False
    if item.get("claim_status") != "OPEN" or item.get("is_archived"):
        return False
    if int(item.get("claimed_count") or 0) >= int(item.get("max_claims") or 0):
        return False
    deadline = item.get("deadline")
    if not deadline:
        return True
    parsed = datetime.fromisoformat(deadline)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > datetime.now(timezone.utc)


async def _hall(client: AsyncClient, user: dict, **params) -> dict:
    resp = await client.get(
        HALL,
        headers=auth_header(user),
        params={"page": 1, "page_size": 100, **params},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _digest(client: AsyncClient, user: dict) -> dict:
    resp = await client.get(DIGEST, headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 核心回归：OLDEST + 发布者大量历史 COMPLETED 单不得填满第一页
# ---------------------------------------------------------------------------


async def test_oldest_hall_page_one_is_not_filled_with_publisher_history(
    client: AsyncClient, db_session, admin_user: dict, registered_user: dict
):
    """事故复现：发布者 120 张旧 COMPLETED 单 + 2 张可抢单，OLDEST 模式。

    修复前：第 1 页 100 条被发布者自己的旧单占满（total=122），前端过滤后
    大厅为空。修复后：大厅只有 2 张可抢单，且每一条都满足前端可抢判定。
    """
    publisher = registered_user
    await _set_sort_mode(client, admin_user, "OLDEST")
    history_ids, claimable_ids = await _seed_publisher_with_history(
        client, db_session, admin_user, publisher
    )

    body = await _hall(client, publisher)
    ids = [item["id"] for item in body["items"]]

    # 大厅不再包含自己的历史单——正是这些单把第一页填满了。
    assert set(history_ids).isdisjoint(ids)
    # 可抢单必须出现在大厅里（不是靠「自己的单」分支，而是真的可抢）。
    assert set(claimable_ids) <= set(ids)
    # 分页总数也必须是可抢口径，而不是「可抢 + 自己的历史」。
    assert body["total"] == len(claimable_ids)
    assert body["hall_count"] == len(claimable_ids)
    # 最关键的断言：返回的每一条都必须是前端会渲染的可抢订单。
    assert ids, body
    assert all(_is_claimable(item) for item in body["items"])
    assert body["hall_sort_mode"] == "OLDEST"


async def test_hall_digest_count_matches_claimable_orders(
    client: AsyncClient, db_session, admin_user: dict, registered_user: dict
):
    """摘要 count 与大厅列表 total 同口径：历史单不计入，客户端的跳过刷新判断可靠。"""
    publisher = registered_user
    await _seed_publisher_with_history(client, db_session, admin_user, publisher)

    digest = await _digest(client, publisher)
    listing = await _hall(client, publisher)

    assert digest["count"] == len(listing["items"])
    assert digest["count"] == listing["total"] == 2
    assert listing["hall_count"] == digest["count"]
    assert listing["hall_max_updated_at"] == digest["max_updated_at"]
    assert listing["hall_earliest_deadline"] == digest["earliest_deadline"]


# ---------------------------------------------------------------------------
# 「我的派单」与后台派单管理不受影响
# ---------------------------------------------------------------------------


async def test_publisher_still_sees_own_history_via_mine_published(
    client: AsyncClient, db_session, admin_user: dict, registered_user: dict
):
    """自己的历史单不会消失：``mine_published=true`` 仍列出（且保持倒序）。"""
    publisher = registered_user
    await _fund(client, admin_user, publisher, 500)
    await _set_sort_mode(client, admin_user, "OLDEST")

    created = [
        await _make_order(client, publisher, title=f"我的派单-{index}")
        for index in range(3)
    ]
    await _set_created_at(db_session, created[0]["id"], _T1)
    await _set_created_at(db_session, created[1]["id"], _T2)
    await _set_created_at(db_session, created[2]["id"], _T3)
    # 其中一张完结：历史单必须仍能在「我的派单」里看到。
    await db_session.execute(
        update(Order)
        .where(Order.id == created[0]["id"])
        .values(status=OrderStatus.COMPLETED)
    )
    await db_session.commit()

    body = await _hall(client, publisher, mine_published="true")
    assert [item["id"] for item in body["items"]] == [
        created[2]["id"],
        created[1]["id"],
        created[0]["id"],
    ]
    # 我的派单不是大厅口径：排序模式与摘要基线都不下发
    assert body["hall_sort_mode"] is None
    assert body["hall_count"] is None

    # 同一张完结单不再出现在公开大厅里
    hall = await _hall(client, publisher)
    assert created[0]["id"] not in [item["id"] for item in hall["items"]]


async def test_admin_dispatch_list_still_sees_non_claimable_orders(
    client: AsyncClient, db_session, admin_user: dict, registered_user: dict
):
    """后台派单管理必须仍是全量视图（COMPLETED/CANCELLED 等历史单都在）。"""
    publisher = registered_user
    history_ids, claimable_ids = await _seed_publisher_with_history(
        client, db_session, admin_user, publisher
    )

    resp = await client.get(
        ADMIN_ORDERS,
        headers=auth_header(admin_user),
        params={"page": 1, "page_size": 100},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    listed = [item["id"] for item in body["items"]]
    assert body["total"] == len(history_ids) + len(claimable_ids)

    # 每页上限 100，共 122 单 → 需要翻到第 2 页才能看到最早的历史单。
    second = await client.get(
        ADMIN_ORDERS,
        headers=auth_header(admin_user),
        params={"page": 2, "page_size": 100},
    )
    assert second.status_code == 200, second.text
    listed += [item["id"] for item in second.json()["items"]]

    # 后台列表不裁剪：旧的 COMPLETED 单必须还能看到。
    assert set(claimable_ids) <= set(listed)
    assert set(history_ids) <= set(listed)
    statuses = {
        item["status"]
        for item in body["items"] + second.json()["items"]
    }
    assert "COMPLETED" in statuses


# ---------------------------------------------------------------------------
# 管理员浏览公开大厅：同样只看可抢订单（不再特判为「不过滤」）
# ---------------------------------------------------------------------------


async def test_admin_public_hall_is_claimable_only(
    client: AsyncClient, db_session, admin_user: dict
):
    """管理员浏览公开大厅也不得看到自己的历史单（否则大厅同样会被填满）。"""
    await _insert_history_orders(db_session, admin_user, 3)
    claimable = await _make_order(client, admin_user, title="管理员可抢单")

    body = await _hall(client, admin_user)
    ids = [item["id"] for item in body["items"]]

    assert claimable["id"] in ids
    assert body["total"] == 1
    assert body["hall_count"] == 1
    assert all(_is_claimable(item) for item in body["items"])

    # 摘要同口径：管理员同样只数可抢订单
    digest = await _digest(client, admin_user)
    assert digest["count"] == 1


# ---------------------------------------------------------------------------
# 指定派单：大厅里只对指定人可见
# ---------------------------------------------------------------------------


async def test_designated_order_visible_only_to_designated_user_in_hall(
    client: AsyncClient, db_session, admin_user: dict, registered_user: dict,
    booster_user: dict, make_captcha
):
    publisher = registered_user
    target = booster_user
    await _fund(client, admin_user, publisher, 500)
    resp = await client.post(
        "/auth/register",
        json={
            "email": "hall-public-third@example.com",
            "username": "HallPublicThird",
            "password": "Passw0rd123",
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    third = resp.json()

    designated = await _make_order(
        client,
        publisher,
        title="指定派单-公开大厅",
        designated_booster_id=target["user"]["id"],
        max_claims=1,
    )

    target_ids = [item["id"] for item in (await _hall(client, target))["items"]]
    third_ids = [item["id"] for item in (await _hall(client, third))["items"]]
    publisher_ids = [
        item["id"] for item in (await _hall(client, publisher))["items"]
    ]

    assert designated["id"] in target_ids
    assert designated["id"] not in third_ids
    # 发布人自己的指定单不进大厅（它不可抢，属于「我的派单」）
    assert designated["id"] not in publisher_ids

    assert (await _digest(client, target))["count"] == 1
    assert (await _digest(client, third))["count"] == 0
    assert (await _digest(client, publisher))["count"] == 0
