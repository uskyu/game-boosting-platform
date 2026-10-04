"""P2 后台工具测试：管理员调保证金 / 查看某用户的全部接单记录。"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select

from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from tests.conftest import auth_header


# ── helpers ──────────────────────────────────────────────────────────────

async def _register(client: AsyncClient, email: str, username: str) -> dict:
    from app.services import captcha_service
    captcha_id, _ = captcha_service.create()
    code, _ = captcha_service._store[captcha_id]
    resp = await client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": "Passw0rd123",
              "captcha_id": captcha_id, "captcha_code": code},
    )
    assert resp.status_code in (200, 201)
    return resp.json()


async def _create_order(
    client: AsyncClient,
    user_data: dict,
    game_name: str = "王者荣耀",
    price: str = "100.00",
    max_claims: int = 1,
) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": game_name,
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
            "max_claims": max_claims,
        },
        headers=auth_header(user_data),
    )
    assert resp.status_code == 201
    return resp.json()


async def _set_deposit(db_session, user_id: int, amount: Decimal) -> None:
    """直接给用户写一个带初始保证金的钱包，省去划转流程。"""
    wallet = Wallet(user_id=user_id, deposit_balance=amount)
    db_session.add(wallet)
    await db_session.commit()


async def _tx_rows(db_session, user_id: int) -> list[WalletTransaction]:
    result = await db_session.execute(
        select(WalletTransaction)
        .join(Wallet, WalletTransaction.wallet_id == Wallet.id)
        .where(Wallet.user_id == user_id)
        .order_by(WalletTransaction.id.asc())
    )
    return list(result.scalars().all())


# ── adjust-deposit ───────────────────────────────────────────────────────

async def test_adjust_deposit_requires_admin(client: AsyncClient, registered_user: dict):
    user_id = registered_user["user"]["id"]
    response = await client.post(
        f"/admin/users/{user_id}/adjust-deposit",
        json={"delta": "10.00", "remark": "测试存入"},
        headers=auth_header(registered_user),
    )
    assert response.status_code == 403


async def test_adjust_deposit_user_not_found(client: AsyncClient, admin_user: dict):
    response = await client.post(
        "/admin/users/999999999/adjust-deposit",
        json={"delta": "10.00", "remark": "测试存入"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 404
    assert "用户不存在" in response.json()["detail"]


async def test_adjust_deposit_rejects_zero_delta(client: AsyncClient, registered_user: dict, admin_user: dict):
    response = await client.post(
        f"/admin/users/{registered_user['user']['id']}/adjust-deposit",
        json={"delta": "0", "remark": "零调整"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400
    assert "不能为0" in response.json()["detail"]


async def test_adjust_deposit_rejects_negative_result(
    client: AsyncClient, registered_user: dict, admin_user: dict, db_session
):
    user_id = registered_user["user"]["id"]
    await _set_deposit(db_session, user_id, Decimal("10.00"))
    response = await client.post(
        f"/admin/users/{user_id}/adjust-deposit",
        json={"delta": "-50.00", "remark": "超额扣出"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400
    assert "保证金余额不足" in response.json()["detail"]

    # 扣减到 0 本身是允许的（不得为负）
    response = await client.post(
        f"/admin/users/{user_id}/adjust-deposit",
        json={"delta": "-10.00", "remark": "全额扣出"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["deposit_balance"] == "0.00"


async def test_adjust_deposit_credit_and_debit(
    client: AsyncClient, registered_user: dict, admin_user: dict, db_session
):
    user_id = registered_user["user"]["id"]
    await _set_deposit(db_session, user_id, Decimal("20.00"))

    # 存入 +15.50：只动保证金，可用余额不变
    response = await client.post(
        f"/admin/users/{user_id}/adjust-deposit",
        json={"delta": "15.50", "remark": "活动补偿存入"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["deposit_balance"] == "35.50"
    assert data["available"] == "0.00"
    assert data["transaction_id"]

    # 扣出 5.50
    response = await client.post(
        f"/admin/users/{user_id}/adjust-deposit",
        json={"delta": "-5.50", "remark": "违规扣减"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["deposit_balance"] == "30.00"
    assert data["available"] == "0.00"

    # 流水：两条 DEPOSIT_TRANSFER_IN / OUT，操作人与备注齐全
    rows = await _tx_rows(db_session, user_id)
    assert len(rows) == 2
    assert rows[0].type == WalletTransactionType.DEPOSIT_TRANSFER_IN
    assert rows[0].amount == Decimal("15.50")
    assert rows[0].operator_id == admin_user["user"]["id"]
    assert rows[0].remark == "活动补偿存入"
    assert rows[1].type == WalletTransactionType.DEPOSIT_TRANSFER_OUT
    assert rows[1].amount == Decimal("-5.50")
    assert rows[1].operator_id == admin_user["user"]["id"]
    assert rows[1].remark == "违规扣减"

    # 明细接口能读到保证金流水（前端「明细」弹窗复用）
    response = await client.get(
        f"/admin/users/{user_id}/transactions", headers=auth_header(admin_user)
    )
    assert response.status_code == 200
    types = [item["type"] for item in response.json()["items"]]
    assert "DEPOSIT_TRANSFER_IN" in types
    assert "DEPOSIT_TRANSFER_OUT" in types


async def test_adjust_deposit_requires_remark(client: AsyncClient, registered_user: dict, admin_user: dict):
    response = await client.post(
        f"/admin/users/{registered_user['user']['id']}/adjust-deposit",
        json={"delta": "10.00", "remark": "   "},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 422


# ── user orders (claims) ─────────────────────────────────────────────────

async def test_user_orders_requires_admin(client: AsyncClient, registered_user: dict):
    response = await client.get(
        f"/admin/users/{registered_user['user']['id']}/orders",
        headers=auth_header(registered_user),
    )
    assert response.status_code == 403


async def test_user_orders_user_not_found(client: AsyncClient, admin_user: dict):
    response = await client.get(
        "/admin/users/999999999/orders", headers=auth_header(admin_user)
    )
    assert response.status_code == 404


async def test_user_orders_empty_for_user_without_claims(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    response = await client.get(
        f"/admin/users/{registered_user['user']['id']}/orders",
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["pages"] == 0


async def test_user_orders_lists_claims_with_filters(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    booster_id = booster_user["user"]["id"]

    # 订单1：指派后保持 CLAIMED；订单2：交付后 DELIVERED，审核通过后 SETTLED
    order_1 = await _create_order(client, admin_user, game_name="王者荣耀")
    order_2 = await _create_order(client, admin_user, game_name="英雄联盟")
    resp = await client.put(
        f"/admin/orders/{order_1['id']}/assign",
        json={"booster_id": booster_id, "reason": "测试指派"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    resp = await client.put(
        f"/admin/orders/{order_2['id']}/assign",
        json={"booster_id": booster_id, "reason": "测试指派"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200

    resp = await client.put(
        f"/orders/{order_2['id']}/deliver",
        json={"delivery_note": "已完成"},
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200

    resp = await client.get(
        f"/orders/{order_2['id']}/claims", headers=auth_header(admin_user)
    )
    claim_id = resp.json()["items"][0]["id"]
    resp = await client.put(
        f"/orders/{order_2['id']}/claims/{claim_id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "SETTLED"

    headers = auth_header(admin_user)

    # 全部：两条接单记录，各带订单摘要
    response = await client.get(f"/admin/users/{booster_id}/orders", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["items"][0]["order"]["id"] == order_2["id"]  # 按接单记录 ID 倒序
    assert all(item["order"]["game_name"] for item in data["items"])
    assert {item["status"] for item in data["items"]} == {"CLAIMED", "SETTLED"}

    # 状态筛选：进行中
    response = await client.get(
        f"/admin/users/{booster_id}/orders", params={"status": "CLAIMED"}, headers=headers
    )
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["order"]["id"] == order_1["id"]
    assert data["items"][0]["status"] == "CLAIMED"

    # 状态筛选：已结算
    response = await client.get(
        f"/admin/users/{booster_id}/orders", params={"status": "SETTLED"}, headers=headers
    )
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["order"]["id"] == order_2["id"]
    assert data["items"][0]["status"] == "SETTLED"

    # 搜索：按订单号（q 走 list_my_claims 的数字匹配）
    response = await client.get(
        f"/admin/users/{booster_id}/orders", params={"q": f"#{order_1['id']}"}, headers=headers
    )
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["order"]["id"] == order_1["id"]

    # 搜索：按游戏名
    response = await client.get(
        f"/admin/users/{booster_id}/orders", params={"q": "英雄联盟"}, headers=headers
    )
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["order"]["id"] == order_2["id"]


async def test_user_orders_returns_only_target_user_claims(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """订单归属校验：只返回目标用户的接单记录，不含其他打手。"""
    other = await _register(client, "other_booster@example.com", "OtherBooster")
    for booster in (booster_user, other):
        order = await _create_order(client, admin_user)
        resp = await client.put(
            f"/admin/orders/{order['id']}/assign",
            json={"booster_id": booster["user"]["id"], "reason": "测试指派"},
            headers=auth_header(admin_user),
        )
        assert resp.status_code == 200

    response = await client.get(
        f"/admin/users/{other['user']['id']}/orders", headers=auth_header(admin_user)
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    booster_ids = {item["booster_id"] for item in data["items"]}
    assert booster_ids == {other["user"]["id"]}
    assert booster_user["user"]["id"] not in booster_ids


async def test_user_orders_delivered_filter(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """状态筛选：待确认（DELIVERED）。"""
    booster_id = booster_user["user"]["id"]
    order = await _create_order(client, admin_user)
    resp = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": booster_id, "reason": "测试指派"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    resp = await client.put(
        f"/orders/{order['id']}/deliver",
        json={"delivery_note": "已完成"},
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 200

    response = await client.get(
        f"/admin/users/{booster_id}/orders",
        params={"status": "DELIVERED"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "DELIVERED"
    assert data["items"][0]["order"]["id"] == order["id"]
