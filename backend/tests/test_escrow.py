"""用户发单托管 + 炸单赔偿金资金流冒烟测试。

覆盖：发单冻结托管、余额不足拒发、接单冻结赔偿金、审核打款（含炸单扣除）
三条主链路。金额断言到分，防止资金流回归。
"""

from decimal import Decimal

from httpx import AsyncClient

from tests.conftest import auth_header


async def _register(client: AsyncClient, email: str, username: str) -> dict:
    resp = await client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": "Passw0rd123"},
    )
    assert resp.status_code in (200, 201)
    return resp.json()


async def _adjust_balance(client: AsyncClient, admin_user: dict, user: dict, amount: str) -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "测试充值"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200


async def _wallet(client: AsyncClient, user: dict) -> dict:
    resp = await client.get("/wallet", headers=auth_header(user))
    assert resp.status_code == 200
    return resp.json()


async def test_user_publish_escrow_freezes_balance(
    client: AsyncClient, admin_user: dict
):
    """普通用户发单冻结 price × max_claims；余额不足 400。"""
    publisher = await _register(client, "escrow.pub@example.com", "EscrowPub")
    await _adjust_balance(client, admin_user, publisher, "1000.00")

    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "price": "100.00",
            "max_claims": 3,
            "boss_contact": "boss-wechat-001",
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["boss_contact"] == "boss-wechat-001"  # 发布人可见

    wallet = await _wallet(client, publisher)
    assert Decimal(str(wallet["available_balance"])) == Decimal("700.00")
    assert Decimal(str(wallet["frozen_balance"])) == Decimal("300.00")

    # 余额不足的另一个用户发单被拒
    poor = await _register(client, "escrow.poor@example.com", "EscrowPoor")
    resp = await client.post(
        "/orders/create",
        json={"game_name": "王者荣耀", "price": "100.00", "max_claims": 3},
        headers=auth_header(poor),
    )
    assert resp.status_code == 400
    assert "余额不足" in resp.json()["detail"]


async def test_claim_freezes_compensation_deposit(
    client: AsyncClient, admin_user: dict
):
    """带炸单赔偿金的订单：接单冻结赔偿金；余额不足不能接。"""
    publisher = await _register(client, "dep.pub@example.com", "DepPub")
    await _adjust_balance(client, admin_user, publisher, "1000.00")

    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "price": "100.00",
            "max_claims": 2,
            "compensation_amount": "50.00",
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201
    order_id = resp.json()["id"]

    booster = await _register(client, "dep.boost@example.com", "DepBoost")
    await _adjust_balance(client, admin_user, booster, "100.00")

    resp = await client.put(f"/orders/{order_id}/accept", headers=auth_header(booster))
    assert resp.status_code == 200

    wallet = await _wallet(client, booster)
    assert Decimal(str(wallet["available_balance"])) == Decimal("50.00")
    assert Decimal(str(wallet["frozen_balance"])) == Decimal("50.00")

    # 未接单者看不到老板ID；接单者详情里 my_claim 存在
    stranger = await _register(client, "dep.other@example.com", "DepOther")
    resp = await client.get(f"/orders/{order_id}", headers=auth_header(stranger))
    assert resp.status_code == 200
    assert resp.json()["boss_contact"] is None

    # 没有赔偿金余额的打手接单被拒
    poor_booster = await _register(client, "dep.poorb@example.com", "DepPoorB")
    resp = await client.put(f"/orders/{order_id}/accept", headers=auth_header(poor_booster))
    assert resp.status_code == 400
    assert "炸单赔偿金" in resp.json()["detail"]


async def test_settle_pays_and_deducts_compensation(
    client: AsyncClient, admin_user: dict
):
    """审核打款：打手入账订单金额、赔偿金按扣除结算、发布人托管划扣。"""
    publisher = await _register(client, "set.pub@example.com", "SetPub")
    await _adjust_balance(client, admin_user, publisher, "1000.00")

    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "price": "100.00",
            "max_claims": 1,
            "compensation_amount": "50.00",
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201
    order_id = resp.json()["id"]

    booster = await _register(client, "set.boost@example.com", "SetBoost")
    await _adjust_balance(client, admin_user, booster, "100.00")
    resp = await client.put(f"/orders/{order_id}/accept", headers=auth_header(booster))
    assert resp.status_code == 200

    resp = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "已完成全部目标"},
        headers=auth_header(booster),
    )
    assert resp.status_code == 200

    resp = await client.get(f"/orders/{order_id}/claims", headers=auth_header(admin_user))
    assert resp.status_code == 200
    claim_id = resp.json()["items"][0]["id"] if isinstance(resp.json(), dict) else resp.json()[0]["id"]

    # 炸单：扣除 20 赔偿金，其余 30 返还
    resp = await client.put(
        f"/orders/{order_id}/claims/{claim_id}/review",
        json={"action": "approve", "deduction": "20.00", "note": "炸单扣款"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200

    wallet = await _wallet(client, booster)
    # 100(初始) - 50(冻结) + 100(订单入账) + 30(赔偿金返还) = 180 可用，冻结归零
    assert Decimal(str(wallet["available_balance"])) == Decimal("180.00")
    assert Decimal(str(wallet["frozen_balance"])) == Decimal("0.00")

    # 发布人：托管 100 已划扣给打手（1000 - 100 冻结，结算后释放）
    pub_wallet = await _wallet(client, publisher)
    assert Decimal(str(pub_wallet["available_balance"])) == Decimal("900.00")
    assert Decimal(str(pub_wallet["frozen_balance"])) == Decimal("0.00")

    # 单名额订单结算后自动完结
    resp = await client.get(f"/orders/{order_id}", headers=auth_header(admin_user))
    assert resp.json()["status"] == "COMPLETED"


async def test_publisher_sees_claim_counts_in_detail_and_list(
    client: AsyncClient, admin_user: dict
):
    """A regular publisher sees pending/settled counts on detail and lists."""
    publisher = await _register(client, "counts.pub@example.com", "CountsPub")
    await _adjust_balance(client, admin_user, publisher, "1000.00")

    order_response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "price": "100.00",
            "max_claims": 1,
            "boss_contact": "counts-boss-contact",
        },
        headers=auth_header(publisher),
    )
    assert order_response.status_code == 201
    order_id = order_response.json()["id"]

    claim_order_response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "price": "100.00",
            "boss_contact": "admin-boss-contact",
        },
        headers=auth_header(admin_user),
    )
    assert claim_order_response.status_code == 201
    claim_order_id = claim_order_response.json()["id"]

    booster = await _register(client, "counts.booster@example.com", "CountsBooster")
    assert (
        await client.put(
            f"/orders/{order_id}/accept", headers=auth_header(booster)
        )
    ).status_code == 200
    booster_detail = await client.get(
        f"/orders/{order_id}", headers=auth_header(booster)
    )
    assert booster_detail.status_code == 200
    assert booster_detail.json()["my_claim"] is not None
    assert booster_detail.json()["boss_contact"] == "counts-boss-contact"

    assert (
        await client.put(
            f"/orders/{order_id}/deliver", headers=auth_header(booster)
        )
    ).status_code == 200

    # The same regular user publishes one order and claims another, so both
    # publisher counts and my_claim must coexist in one batched response.
    publisher_claim = await client.put(
        f"/orders/{claim_order_id}/accept", headers=auth_header(publisher)
    )
    assert publisher_claim.status_code == 200

    publisher_detail = await client.get(
        f"/orders/{order_id}", headers=auth_header(publisher)
    )
    assert publisher_detail.status_code == 200
    assert publisher_detail.json()["pending_review_count"] == 1
    assert publisher_detail.json()["settled_count"] == 0

    publisher_list = await client.get(
        "/orders/?mine_published=true&page=1&page_size=100",
        headers=auth_header(publisher),
    )
    assert publisher_list.status_code == 200
    listed = {item["id"]: item for item in publisher_list.json()["items"]}
    assert listed[order_id]["pending_review_count"] == 1
    assert listed[order_id]["settled_count"] == 0

    all_orders = await client.get(
        "/orders/?page=1&page_size=100", headers=auth_header(publisher)
    )
    assert all_orders.status_code == 200
    listed = {item["id"]: item for item in all_orders.json()["items"]}
    assert listed[order_id]["pending_review_count"] == 1
    assert listed[claim_order_id]["my_claim"] is not None
    assert listed[claim_order_id]["my_claim"]["booster_id"] == publisher["user"]["id"]
    assert listed[claim_order_id]["boss_contact"] == "admin-boss-contact"

    combined_detail = await client.get(
        f"/orders/{claim_order_id}", headers=auth_header(publisher)
    )
    assert combined_detail.status_code == 200
    assert combined_detail.json()["pending_review_count"] == 0
    assert combined_detail.json()["settled_count"] == 0
    assert combined_detail.json()["my_claim"] is not None

    claims_response = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(publisher)
    )
    assert claims_response.status_code == 200
    claim_id = claims_response.json()["items"][0]["id"]
    review_response = await client.put(
        f"/orders/{order_id}/claims/{claim_id}/review",
        json={"action": "approve"},
        headers=auth_header(publisher),
    )
    assert review_response.status_code == 200
    assert review_response.json()["status"] == "SETTLED"

    publisher_detail = await client.get(
        f"/orders/{order_id}", headers=auth_header(publisher)
    )
    assert publisher_detail.json()["pending_review_count"] == 0
    assert publisher_detail.json()["settled_count"] == 1

    publisher_list = await client.get(
        "/orders/?mine_published=true&page=1&page_size=100",
        headers=auth_header(publisher),
    )
    listed = {item["id"]: item for item in publisher_list.json()["items"]}
    assert listed[order_id]["pending_review_count"] == 0
    assert listed[order_id]["settled_count"] == 1
