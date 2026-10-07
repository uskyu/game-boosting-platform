"""取消协商赔偿的扣除来源，与「申请取消中」状态/筛选。

老板 2026-10-07 反馈：
1. 无押金打手没有保证金可扣，就无法参与取消赔偿 → 扣除顺序改为
   「该名额仍在冻结的炸单赔偿金 → 可用余额 → 保证金」，发起提议与
   同意裁决都按这个合计校验可扣款项；
2. 取消申请挂起期间订单仍显示「进行中」→ 订单与名额打 cancel_pending
   标记，订单/报名列表均支持 status=CANCELLING 筛选。
"""
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from tests.conftest import auth_header


async def _register(client: AsyncClient, make_captcha, email: str, username: str) -> dict:
    response = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "TestPass123",
            **make_captcha(),
        },
    )
    assert response.status_code in (200, 201), response.text
    login = await client.post(
        "/auth/login", json={"email": email, "password": "TestPass123"}
    )
    assert login.status_code == 200, login.text
    return login.json()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: int) -> None:
    response = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "test fund"},
        headers=auth_header(admin_user),
    )
    assert response.status_code in (200, 201), response.text


async def _deposit(client: AsyncClient, admin_user: dict, user: dict, amount: int) -> None:
    response = await client.post(
        f"/admin/users/{user['user']['id']}/adjust-deposit",
        json={"delta": amount, "remark": "test deposit"},
        headers=auth_header(admin_user),
    )
    assert response.status_code in (200, 201), response.text


async def _publish(
    client: AsyncClient,
    publisher: dict,
    *,
    max_claims: int = 1,
    compensation_amount: str | None = None,
) -> dict:
    payload: dict = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": "100.00",
        "max_claims": max_claims,
    }
    if compensation_amount is not None:
        payload["compensation_amount"] = compensation_amount
    response = await client.post(
        "/orders/create", json=payload, headers=auth_header(publisher)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _accept(client: AsyncClient, booster: dict, order_id: int) -> None:
    response = await client.put(f"/orders/{order_id}/accept", headers=auth_header(booster))
    assert response.status_code == 200, response.text


async def _claim_id(client: AsyncClient, publisher: dict, order_id: int, booster_id: int) -> int:
    response = await client.get(f"/orders/{order_id}/claims", headers=auth_header(publisher))
    assert response.status_code == 200, response.text
    return next(
        item["id"] for item in response.json()["items"] if item["booster_id"] == booster_id
    )


async def _wallet(db: AsyncSession, user_id: int) -> Wallet:
    db.expire_all()
    result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    return result.scalar_one()


async def _create_request(
    client: AsyncClient,
    order_id: int,
    claim_id: int,
    *,
    reason: str,
    compensation_amount: float,
    user: dict,
):
    return await client.post(
        f"/orders/{order_id}/cancel-requests",
        json={
            "claim_id": claim_id,
            "reason": reason,
            "compensation_amount": compensation_amount,
        },
        headers=auth_header(user),
    )


async def test_no_deposit_booster_compensates_from_available_balance(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """无押金（保证金 0）打手也能参与取消赔偿：从可用余额扣。"""
    publisher = await _register(
        client, make_captcha, "nodeposit-pub@example.com", "NoDepositPub"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher, 200)
    await _fund(client, admin_user, booster_user, 100)
    order = await _publish(client, publisher)
    await _accept(client, booster_user, order["id"])
    claim_id = await _claim_id(client, publisher, order["id"], booster_id)

    # 旧逻辑：保证金 0 直接 400「接单人保证金不足」
    created = await _create_request(
        client,
        order["id"],
        claim_id,
        reason="无押金打手也要能协商取消",
        compensation_amount=30,
        user=publisher,
    )
    assert created.status_code == 200, created.text

    await db_session.rollback()
    decision = await client.post(
        f"/orders/{order['id']}/cancel-requests/{created.json()['id']}/decision",
        json={"action": "approve"},
        headers=auth_header(booster_user),
    )
    assert decision.status_code == 200, decision.text

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.available_balance == Decimal("70.00")
    assert booster_wallet.deposit_balance == Decimal("0.00")
    publisher_wallet = await _wallet(db_session, publisher_id)
    # 200 入账 - 100 托管 + 100 退回 + 30 赔偿
    assert publisher_wallet.available_balance == Decimal("230.00")
    assert publisher_wallet.frozen_balance == Decimal("0.00")


async def test_cancel_compensation_order_frozen_then_available_then_deposit(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """扣除顺序：先该名额冻结的炸单赔偿金，再可用余额，最后保证金。"""
    publisher = await _register(
        client, make_captcha, "chain-pub@example.com", "ChainPub"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher, 200)
    await _fund(client, admin_user, booster_user, 100)
    await _deposit(client, admin_user, booster_user, 50)
    # 订单炸单赔偿 ¥20：接单即从可用余额冻结
    order = await _publish(client, publisher, compensation_amount="20.00")
    await _accept(client, booster_user, order["id"])

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.available_balance == Decimal("80.00")
    assert booster_wallet.frozen_balance == Decimal("20.00")

    # 报名名单给出可扣款项上限：冻结 20 + 可用 80 + 保证金 50 = 150
    claims = await client.get(
        f"/orders/{order['id']}/claims", headers=auth_header(publisher)
    )
    assert claims.status_code == 200, claims.text
    claim = next(
        item for item in claims.json()["items"] if item["booster_id"] == booster_id
    )
    assert claim["id"] is not None
    assert Decimal(claim["booster_compensation_available"]) == Decimal("150.00")

    # 约定 30：冻结 20 全扣 + 可用余额补 10，保证金不动
    created = await _create_request(
        client,
        order["id"],
        claim["id"],
        reason="按冻结赔付优先的顺序扣除",
        compensation_amount=30,
        user=publisher,
    )
    assert created.status_code == 200, created.text

    await db_session.rollback()
    decision = await client.post(
        f"/orders/{order['id']}/cancel-requests/{created.json()['id']}/decision",
        json={"action": "approve"},
        headers=auth_header(booster_user),
    )
    assert decision.status_code == 200, decision.text

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.frozen_balance == Decimal("0.00")
    assert booster_wallet.available_balance == Decimal("70.00")
    assert booster_wallet.deposit_balance == Decimal("50.00")
    publisher_wallet = await _wallet(db_session, publisher_id)
    assert publisher_wallet.available_balance == Decimal("230.00")

    deduction = await db_session.execute(
        select(WalletTransaction)
        .join(Wallet, WalletTransaction.wallet_id == Wallet.id)
        .where(
            WalletTransaction.order_id == order["id"],
            WalletTransaction.booster_id == booster_id,
            WalletTransaction.type == WalletTransactionType.CANCEL_COMPENSATION_DEDUCT,
        )
    )
    assert deduction.scalar_one().amount == Decimal("-30.00")


async def test_partial_frozen_hold_is_released_after_cancel(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """赔偿只消耗一部分冻结赔付时，剩余部分原样解冻回可用余额。"""
    publisher = await _register(
        client, make_captcha, "release-pub@example.com", "ReleasePub"
    )
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher, 200)
    await _fund(client, admin_user, booster_user, 100)
    order = await _publish(client, publisher, compensation_amount="20.00")
    await _accept(client, booster_user, order["id"])
    claim_id = await _claim_id(client, publisher, order["id"], booster_id)

    created = await _create_request(
        client,
        order["id"],
        claim_id,
        reason="只扣一部分冻结赔付",
        compensation_amount=10,
        user=publisher,
    )
    assert created.status_code == 200, created.text

    await db_session.rollback()
    decision = await client.post(
        f"/orders/{order['id']}/cancel-requests/{created.json()['id']}/decision",
        json={"action": "approve"},
        headers=auth_header(booster_user),
    )
    assert decision.status_code == 200, decision.text

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    # 冻结 20 里扣 10，剩 10 解冻回可用余额：可用 = 80 + 10 = 90（只损失约定的 10）
    assert booster_wallet.frozen_balance == Decimal("0.00")
    assert booster_wallet.available_balance == Decimal("90.00")
    assert booster_wallet.deposit_balance == Decimal("0.00")

    release = await db_session.execute(
        select(WalletTransaction)
        .join(Wallet, WalletTransaction.wallet_id == Wallet.id)
        .where(
            WalletTransaction.order_id == order["id"],
            WalletTransaction.booster_id == booster_id,
            WalletTransaction.type == WalletTransactionType.DEPOSIT_RELEASE,
        )
    )
    assert release.scalar_one().amount == Decimal("10.00")


async def test_cancel_compensation_rejects_when_nothing_collectible(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """三个来源都为 0 时：带赔偿的提议 400；0 赔偿仍可免费取消。"""
    publisher = await _register(
        client, make_captcha, "empty-pub@example.com", "EmptyPub"
    )
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher, 200)
    order = await _publish(client, publisher)
    await _accept(client, booster_user, order["id"])
    claim_id = await _claim_id(client, publisher, order["id"], booster_id)

    rejected = await _create_request(
        client,
        order["id"],
        claim_id,
        reason="没有任何可扣款项",
        compensation_amount=30,
        user=publisher,
    )
    assert rejected.status_code == 400, rejected.text
    assert "可扣款项不足" in rejected.json()["detail"]

    free = await _create_request(
        client,
        order["id"],
        claim_id,
        reason="双方协商免费取消名额",
        compensation_amount=0,
        user=publisher,
    )
    assert free.status_code == 200, free.text

    await db_session.rollback()
    decision = await client.post(
        f"/orders/{order['id']}/cancel-requests/{free.json()['id']}/decision",
        json={"action": "approve"},
        headers=auth_header(booster_user),
    )
    assert decision.status_code == 200, decision.text

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.available_balance == Decimal("0.00")


async def test_decide_fails_when_booster_spent_money_after_request(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """提议时有钱、同意前被花光：同意时 400，提示重新协商。"""
    publisher = await _register(
        client, make_captcha, "spent-pub@example.com", "SpentPub"
    )
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher, 200)
    await _fund(client, admin_user, booster_user, 100)
    order = await _publish(client, publisher)
    await _accept(client, booster_user, order["id"])
    claim_id = await _claim_id(client, publisher, order["id"], booster_id)

    created = await _create_request(
        client,
        order["id"],
        claim_id,
        reason="先谈好赔偿金额",
        compensation_amount=30,
        user=publisher,
    )
    assert created.status_code == 200, created.text

    # 打手把钱花掉（管理员调账只动可用余额）
    drain = await client.post(
        f"/admin/wallets/{booster_id}/adjust",
        json={"amount": -100, "reason": "test drain"},
        headers=auth_header(admin_user),
    )
    assert drain.status_code in (200, 201), drain.text

    await db_session.rollback()
    decision = await client.post(
        f"/orders/{order['id']}/cancel-requests/{created.json()['id']}/decision",
        json={"action": "approve"},
        headers=auth_header(booster_user),
    )
    assert decision.status_code == 400, decision.text
    assert "可扣款项" in decision.json()["detail"]

    # 拒绝后订单与名额保持原状
    await db_session.rollback()
    rejected = await client.post(
        f"/orders/{order['id']}/cancel-requests/{created.json()['id']}/decision",
        json={"action": "reject"},
        headers=auth_header(booster_user),
    )
    assert rejected.status_code == 200, rejected.text


async def test_cancel_pending_flag_and_cancelling_filter(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """取消协商挂起：订单/名额带 cancel_pending 标记，列表支持
    status=CANCELLING 筛选，处理后标记与筛选结果同步消失。"""
    publisher = await _register(
        client, make_captcha, "flag-pub@example.com", "FlagPub"
    )
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher, 200)
    order = await _publish(client, publisher)
    await _accept(client, booster_user, order["id"])
    claim_id = await _claim_id(client, publisher, order["id"], booster_id)

    created = await _create_request(
        client,
        order["id"],
        claim_id,
        reason="验证申请取消中状态",
        compensation_amount=0,
        user=publisher,
    )
    assert created.status_code == 200, created.text

    # 订单详情（发单员视角）：cancel_pending=true，订单状态仍 LOCKED
    detail = await client.get(f"/orders/{order['id']}", headers=auth_header(publisher))
    assert detail.status_code == 200, detail.text
    assert detail.json()["cancel_pending"] is True
    assert detail.json()["status"] == "LOCKED"

    # 我的派单：status=CANCELLING 命中该单
    await db_session.rollback()
    cancelling = await client.get(
        "/orders/",
        params={"mine_published": "true", "status": "CANCELLING"},
        headers=auth_header(publisher),
    )
    assert cancelling.status_code == 200, cancelling.text
    items = cancelling.json()["items"]
    assert [item["id"] for item in items] == [order["id"]]
    assert items[0]["cancel_pending"] is True

    # 我的接单（打手视角）：status=CANCELLING 命中该名额
    await db_session.rollback()
    cancelling_claims = await client.get(
        "/orders/claims/mine",
        params={"status": "CANCELLING"},
        headers=auth_header(booster_user),
    )
    assert cancelling_claims.status_code == 200, cancelling_claims.text
    claim_items = cancelling_claims.json()["items"]
    assert len(claim_items) == 1
    assert claim_items[0]["cancel_pending"] is True
    assert claim_items[0]["status"] == "CLAIMED"

    # 无效状态值：中文 422（替代原枚举 422）
    invalid = await client.get(
        "/orders/",
        params={"mine_published": "true", "status": "NOPE"},
        headers=auth_header(publisher),
    )
    assert invalid.status_code == 422, invalid.text

    # 拒绝后：标记与两个筛选都不再命中
    await db_session.rollback()
    rejected = await client.post(
        f"/orders/{order['id']}/cancel-requests/{created.json()['id']}/decision",
        json={"action": "reject"},
        headers=auth_header(booster_user),
    )
    assert rejected.status_code == 200, rejected.text

    detail_after = await client.get(f"/orders/{order['id']}", headers=auth_header(publisher))
    assert detail_after.json()["cancel_pending"] is False

    await db_session.rollback()
    empty_filter = await client.get(
        "/orders/",
        params={"mine_published": "true", "status": "CANCELLING"},
        headers=auth_header(publisher),
    )
    assert empty_filter.json()["total"] == 0

    await db_session.rollback()
    empty_claims = await client.get(
        "/orders/claims/mine",
        params={"status": "CANCELLING"},
        headers=auth_header(booster_user),
    )
    assert empty_claims.json()["total"] == 0
