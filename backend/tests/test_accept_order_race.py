"""接单并发契约回归测试（accept_order 行锁语义）。

锁定 2026-10-09 前端「订单已被抢走」整改配套的后端契约，防止未来改动
破坏接单并发的判定顺序：

1. ``max_claims=1`` 并发接单：订单行 ``SELECT ... FOR UPDATE`` 串行化后，
   恰好一个 200、一个 400「订单已被其他代练接单」，库里只有一条 claim；
2. 旧快照竞态：先 ``GET /orders/{id}`` 看到 PENDING，别人接单后再用该
   快照接单必须 400（锁顺序不回归，详情页数据不再陈旧也有后端兜底）；
3. ``max_claims=3`` 满员后第 4 人接单 → 400「订单接单人数已满」，
   且订单 ``claim_status`` 置 FULL；
4. ``GET /orders/{id}`` 对未报名打手返回的 claim_status/claimed_count/
   max_claims 与 accept 判定一致（防前后端判据漂移）；
5. ``GET /orders/{id}/claims`` 对非发布人打手返回 403（固定契约）。
"""

import asyncio

from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.order import (
    ClaimLifecycleStatus,
    Order,
    OrderClaim,
    OrderStatus,
)
from app.models.user import User, UserRole
from tests.conftest import auth_header

BOOSTER_PASSWORD = "Passw0rd123"

# 接单被拒时「这单已经被人抢走了」的两种文案。max_claims=1 的普通订单被抢后
# claim_status 已同步翻成 FULL，先命中 claim_status 分支（order_service.py:820）；
# 只有 claim_status 仍为 OPEN 的 LOCKED 单（服务卡订单）才命中 LOCKED 分支
# （order_service.py:833）。两者对用户的含义一致，断言时按集合放宽，避免把
# 「检查顺序调整」误判成契约回归；各自的精确文案由下面的专项用例钉住。
TAKEN_MESSAGES = {"订单已被其他代练接单", "订单接单人数已满"}


async def _register_booster(
    client: AsyncClient,
    db_session,
    make_captcha,
    email: str,
    username: str,
) -> dict:
    """Register a user, promote to BOOSTER via DB, re-login for the new role."""
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": BOOSTER_PASSWORD,
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201)

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.role = UserRole.BOOSTER
    user.booster_quota = 5
    await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": email, "password": BOOSTER_PASSWORD}
    )
    assert login.status_code == 200
    return login.json()


async def _create_order(
    client: AsyncClient, publisher: dict, max_claims: int = 1
) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "200.00",
            "max_claims": max_claims,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _accept(client: AsyncClient, order_id: int, booster: dict):
    return await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster)
    )


async def test_concurrent_accept_single_slot_allows_exactly_one(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """max_claims=1：两个打手同时接单，行锁保证恰好一个成功、一个被拒。"""
    order = await _create_order(client, admin_user, max_claims=1)
    order_id = order["id"]

    booster_a = await _register_booster(
        client, db_session, make_captcha, "race_a@example.com", "RaceA"
    )
    booster_b = await _register_booster(
        client, db_session, make_captcha, "race_b@example.com", "RaceB"
    )

    resp_a, resp_b = await asyncio.gather(
        _accept(client, order_id, booster_a),
        _accept(client, order_id, booster_b),
    )

    # 恰好一个 200、一个 400，且 400 属于「已被抢走」家族而不是配额/重复报名
    statuses = sorted([resp_a.status_code, resp_b.status_code])
    assert statuses == [200, 400], (resp_a.status_code, resp_b.status_code)
    winner, loser = (
        (resp_a, resp_b) if resp_a.status_code == 200 else (resp_b, resp_a)
    )
    assert loser.json()["detail"] in TAKEN_MESSAGES

    winner_data = winner.json()
    winner_id = (
        booster_a["user"]["id"]
        if winner is resp_a
        else booster_b["user"]["id"]
    )
    assert winner_data["booster_id"] == winner_id
    assert winner_data["status"] == "LOCKED"
    assert winner_data["claimed_count"] == 1
    assert winner_data["claim_status"] == "FULL"

    # 库里只有一条 claim：并发没有穿透行锁多写名额
    claim_count = (
        await db_session.execute(
            select(func.count(OrderClaim.id)).where(OrderClaim.order_id == order_id)
        )
    ).scalar()
    assert claim_count == 1
    claim = (
        await db_session.execute(
            select(OrderClaim).where(OrderClaim.order_id == order_id)
        )
    ).scalar_one()
    assert claim.booster_id == winner_id


async def test_stale_snapshot_accept_is_rejected_after_another_booster(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """竞态时序：拿着 accept 前的 PENDING 快照后接单必须 400。"""
    order = await _create_order(client, admin_user, max_claims=1)
    order_id = order["id"]

    booster_a = await _register_booster(
        client, db_session, make_captcha, "stale_a@example.com", "StaleA"
    )
    booster_b = await _register_booster(
        client, db_session, make_captcha, "stale_b@example.com", "StaleB"
    )

    # A 先看到「可接单」的旧快照
    snapshot = await client.get(
        f"/orders/{order_id}", headers=auth_header(booster_a)
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "PENDING"
    assert snapshot.json()["claim_status"] == "OPEN"

    # B 在这之后抢到单
    assert (await _accept(client, order_id, booster_b)).status_code == 200

    # A 再凭旧快照接单：行锁重新读取最新行版本，必须被拒
    resp = await _accept(client, order_id, booster_a)
    assert resp.status_code == 400
    assert resp.json()["detail"] in TAKEN_MESSAGES

    # 只有 B 的 claim，订单归属 B
    claims = (
        (
            await db_session.execute(
                select(OrderClaim).where(OrderClaim.order_id == order_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(claims) == 1
    assert claims[0].booster_id == booster_b["user"]["id"]


async def test_locked_single_slot_order_reports_taken_by_other_booster(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """LOCKED + claim_status=OPEN + max_claims=1 → 400「订单已被其他代练接单」。

    这是服务卡订单（create_service_order：status=LOCKED、claimed_count=1、
    claim_status 留模型默认 OPEN）落地后的状态，也是前端 canAcceptOrder 第 5 行
    （status==='LOCKED' && max_claims<=1 → 不可接）对应的后端分支。普通订单
    走 accept 会把 claim_status 同步翻成 FULL 而走到另一个分支，所以这里直接
    构造等价状态来钉住这条文案。
    """
    order = await _create_order(client, admin_user, max_claims=1)
    order_id = order["id"]

    holder = await _register_booster(
        client, db_session, make_captcha, "holder@example.com", "Holder"
    )
    challenger = await _register_booster(
        client, db_session, make_captcha, "challenger@example.com", "Challenger"
    )

    row = (
        await db_session.execute(select(Order).where(Order.id == order_id))
    ).scalar_one()
    row.status = OrderStatus.LOCKED
    row.booster_id = holder["user"]["id"]
    row.claimed_count = 1
    db_session.add(
        OrderClaim(
            order_id=order_id,
            booster_id=holder["user"]["id"],
            status=ClaimLifecycleStatus.CLAIMED,
        )
    )
    await db_session.commit()

    resp = await _accept(client, order_id, challenger)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "订单已被其他代练接单"

    # 库里仍然只有占位打手那一条 claim
    claim_count = (
        await db_session.execute(
            select(func.count(OrderClaim.id)).where(OrderClaim.order_id == order_id)
        )
    ).scalar()
    assert claim_count == 1


async def test_accept_rejected_once_multi_slot_quota_is_full(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """max_claims=3：前三名接单成功，第 4 人 400「订单接单人数已满」。"""
    order = await _create_order(client, admin_user, max_claims=3)
    order_id = order["id"]

    boosters = [
        await _register_booster(
            client,
            db_session,
            make_captcha,
            f"quota_{index}@example.com",
            f"Quota{index}",
        )
        for index in range(4)
    ]

    for booster in boosters[:3]:
        resp = await _accept(client, order_id, booster)
        assert resp.status_code == 200
        assert resp.json()["claimed_count"] <= 3

    # 第 4 人：名额已满
    resp = await _accept(client, order_id, boosters[3])
    assert resp.status_code == 400
    assert resp.json()["detail"] == "订单接单人数已满"

    # 订单翻成 FULL，且仍然只有 3 条 claim
    detail = await client.get(f"/orders/{order_id}", headers=auth_header(admin_user))
    assert detail.status_code == 200
    assert detail.json()["claim_status"] == "FULL"
    assert detail.json()["claimed_count"] == 3
    assert detail.json()["max_claims"] == 3

    claim_count = (
        await db_session.execute(
            select(func.count(OrderClaim.id)).where(OrderClaim.order_id == order_id)
        )
    ).scalar()
    assert claim_count == 3


async def test_order_detail_claim_fields_match_accept_gate(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """详情页 claim 字段与 accept 判定一致：未报名打手看到什么就能接什么。"""
    order = await _create_order(client, admin_user, max_claims=3)
    order_id = order["id"]

    # PENDING / 0 of 3：字段表明可接，accept 也确实放行
    first = await _register_booster(
        client, db_session, make_captcha, "fields_1@example.com", "FieldsOne"
    )
    detail = await client.get(f"/orders/{order_id}", headers=auth_header(first))
    assert detail.status_code == 200
    data = detail.json()
    assert data["status"] == "PENDING"
    assert data["claim_status"] == "OPEN"
    assert data["claimed_count"] == 0
    assert data["max_claims"] == 3
    assert (await _accept(client, order_id, first)).status_code == 200

    # LOCKED 但仍有名额：字段同样表明可接
    second = await _register_booster(
        client, db_session, make_captcha, "fields_2@example.com", "FieldsTwo"
    )
    detail = await client.get(f"/orders/{order_id}", headers=auth_header(second))
    assert detail.status_code == 200
    data = detail.json()
    assert data["status"] == "LOCKED"
    assert data["claim_status"] == "OPEN"
    assert data["claimed_count"] == 1
    assert (await _accept(client, order_id, second)).status_code == 200

    # 满员：字段翻成 FULL，接单同步被拒（口径一致，前端不会误判「可抢」）
    third = await _register_booster(
        client, db_session, make_captcha, "fields_3@example.com", "FieldsThree"
    )
    assert (await _accept(client, order_id, third)).status_code == 200

    fourth = await _register_booster(
        client, db_session, make_captcha, "fields_4@example.com", "FieldsFour"
    )
    resp = await _accept(client, order_id, fourth)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "订单接单人数已满"

    # 满员订单对未报名打手不再大厅可见（403），由发布人视角核对字段
    outsider_view = await client.get(
        f"/orders/{order_id}", headers=auth_header(fourth)
    )
    assert outsider_view.status_code == 403
    publisher_view = await client.get(
        f"/orders/{order_id}", headers=auth_header(admin_user)
    )
    assert publisher_view.json()["claim_status"] == "FULL"
    assert publisher_view.json()["claimed_count"] == 3


async def test_order_claims_endpoint_forbidden_for_non_publisher_booster(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session,
    make_captcha,
):
    """GET /orders/{id}/claims：非发布人（含接了单的打手）一律 403。"""
    order = await _create_order(client, admin_user)
    order_id = order["id"]

    booster = await _register_booster(
        client, db_session, make_captcha, "claims_a@example.com", "ClaimsA"
    )
    assert (await _accept(client, order_id, booster)).status_code == 200

    # 接了单的打手依然不是发布人：403（前端据此跳过请求）
    resp = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(booster)
    )
    assert resp.status_code == 403

    # 与订单无关的普通用户同样 403
    resp = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(registered_user)
    )
    assert resp.status_code == 403

    # 发布人（管理员）可见
    resp = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["booster_id"] == booster["user"]["id"]
