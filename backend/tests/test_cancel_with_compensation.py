"""双方取消协商：发单员提议扣款、收件打手同意后才结算赔偿。"""
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import ClaimLifecycleStatus, ClaimStatus, Order, OrderClaim, OrderStatus
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


async def _publish(client: AsyncClient, publisher: dict, *, max_claims: int = 1) -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "max_claims": max_claims,
        },
        headers=auth_header(publisher),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _claim_id(client: AsyncClient, publisher: dict, order_id: int, booster_id: int) -> int:
    response = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(publisher)
    )
    assert response.status_code == 200, response.text
    claim = next(item for item in response.json()["items"] if item["booster_id"] == booster_id)
    return claim["id"]


async def _wallet(db: AsyncSession, user_id: int) -> Wallet:
    db.expire_all()
    result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    value = result.scalar_one()
    return value


async def test_publisher_request_moves_money_only_after_booster_accepts(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    publisher = await _register(client, make_captcha, "bilateral-pub@example.com", "BilateralPub")
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher, 200)
    await _deposit(client, admin_user, booster_user, 50)
    order = await _publish(client, publisher)

    accepted = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(booster_user)
    )
    assert accepted.status_code == 200, accepted.text
    claim_id = await _claim_id(client, publisher, order["id"], booster_id)

    legacy = await client.post(
        f"/orders/{order['id']}/apply-cancel",
        json={"reason": "旧流程应刷新", "deduction_amount": 30},
        headers=auth_header(publisher),
    )
    assert legacy.status_code == 409, legacy.text

    created = await client.post(
        f"/orders/{order['id']}/cancel-requests",
        json={"claim_id": claim_id, "reason": "双方时间安排冲突", "compensation_amount": 30},
        headers=auth_header(publisher),
    )
    assert created.status_code == 200, created.text
    request = created.json()
    assert request["status"] == "PENDING"
    assert request["requester_role"] == "PUBLISHER"
    assert request["recipient_id"] == booster_id

    # 申请阶段不取消名额，也不移动任何金额。
    detail = await client.get(f"/orders/{order['id']}", headers=auth_header(publisher))
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "LOCKED"
    assert (await _wallet(db_session, booster_id)).deposit_balance == Decimal("50.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("100.00")
    # End the test session's REPEATABLE READ snapshot before observing the
    # other HTTP session's committed decision.
    await db_session.rollback()

    accepted_request = await client.post(
        f"/orders/{order['id']}/cancel-requests/{request['id']}/decision",
        json={"action": "approve"},
        headers=auth_header(booster_user),
    )
    assert accepted_request.status_code == 200, accepted_request.text
    assert accepted_request.json()["status"] == "APPROVED"

    db_session.expire_all()
    order_row = await db_session.get(Order, order["id"])
    assert order_row.status == OrderStatus.CANCELLED
    claims_result = await db_session.execute(
        select(OrderClaim).where(OrderClaim.id == claim_id)
    )
    claim = claims_result.scalar_one()
    assert claim.status == ClaimLifecycleStatus.CANCELLED
    assert claim.approved_deduction == Decimal("30.00")
    assert claim.approved_note == "双方时间安排冲突"
    assert (await _wallet(db_session, booster_id)).deposit_balance == Decimal("20.00")
    publisher_wallet = await _wallet(db_session, publisher_id)
    assert publisher_wallet.available_balance == Decimal("230.00")
    assert publisher_wallet.frozen_balance == Decimal("0.00")

    tx_result = await db_session.execute(
        select(WalletTransaction)
        .join(Wallet, WalletTransaction.wallet_id == Wallet.id)
        .where(
            WalletTransaction.order_id == order["id"],
            WalletTransaction.booster_id == booster_id,
            WalletTransaction.type == WalletTransactionType.CANCEL_COMPENSATION_DEDUCT,
        )
    )
    deduction = tx_result.scalar_one()
    assert deduction.amount == Decimal("-30.00")

    income_result = await db_session.execute(
        select(WalletTransaction)
        .join(Wallet, WalletTransaction.wallet_id == Wallet.id)
        .where(
            WalletTransaction.order_id == order["id"],
            WalletTransaction.booster_id == booster_id,
            WalletTransaction.type == WalletTransactionType.CANCEL_COMPENSATION_IN,
        )
    )
    compensation = income_result.scalar_one()
    assert compensation.amount == Decimal("30.00")


async def test_multi_claim_cancel_only_closes_selected_slot(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    publisher = await _register(client, make_captcha, "multi-pub@example.com", "MultiPub")
    second_booster = await _register(
        client, make_captcha, "multi-booster@example.com", "MultiBooster"
    )
    second_user_id = second_booster["user"]["id"]
    # The authenticated user fixture has BOOSTER role already; promote the new test user.
    from app.models.user import User, UserRole
    second_row = await db_session.get(User, second_user_id)
    second_row.role = UserRole.BOOSTER
    second_row.booster_quota = 5
    await db_session.commit()

    await _fund(client, admin_user, publisher, 400)
    await _deposit(client, admin_user, booster_user, 80)
    await _deposit(client, admin_user, second_booster, 90)
    order = await _publish(client, publisher, max_claims=2)
    for booster in (booster_user, second_booster):
        accepted = await client.put(
            f"/orders/{order['id']}/accept", headers=auth_header(booster)
        )
        assert accepted.status_code == 200, accepted.text
    first_claim_id = await _claim_id(
        client, publisher, order["id"], booster_user["user"]["id"]
    )
    second_claim_id = await _claim_id(client, publisher, order["id"], second_user_id)

    request_response = await client.post(
        f"/orders/{order['id']}/cancel-requests",
        json={"claim_id": first_claim_id, "reason": "该名额无法继续履约", "compensation_amount": 20},
        headers=auth_header(publisher),
    )
    assert request_response.status_code == 200, request_response.text
    request = request_response.json()
    decision = await client.post(
        f"/orders/{order['id']}/cancel-requests/{request['id']}/decision",
        json={"action": "approve"},
        headers=auth_header(booster_user),
    )
    assert decision.status_code == 200, decision.text

    db_session.expire_all()
    order_row = await db_session.get(Order, order["id"])
    assert order_row.status == OrderStatus.LOCKED
    assert order_row.claimed_count == 1
    assert order_row.claim_status == ClaimStatus.OPEN
    claim_result = await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order["id"])
    )
    claims = {claim.id: claim for claim in claim_result.scalars().all()}
    assert claims[first_claim_id].status == ClaimLifecycleStatus.CANCELLED
    assert claims[second_claim_id].status == ClaimLifecycleStatus.CLAIMED
    assert (await _wallet(db_session, booster_user["user"]["id"])).deposit_balance == Decimal("60.00")
    assert (await _wallet(db_session, second_user_id)).deposit_balance == Decimal("90.00")
