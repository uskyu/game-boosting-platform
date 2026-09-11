"""Regression tests for administrator order completion and cancellation."""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select

from app.models.deposit import DepositSetting
from app.models.order import ClaimLifecycleStatus, OrderClaim
from app.models.wallet import WalletTransaction, WalletTransactionType
from app.models.user import User, UserRole
from tests.conftest import auth_header


async def _create_order(
    client: AsyncClient,
    user: dict,
    price: str = "500.00",
    compensation_amount: str | None = None,
) -> dict:
    payload = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": price,
    }
    if compensation_amount is not None:
        payload["compensation_amount"] = compensation_amount
    response = await client.post(
        "/orders/create",
        json=payload,
        headers=auth_header(user),
    )
    assert response.status_code == 201
    return response.json()


async def test_admin_completion_credits_assigned_booster(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
):
    order = await _create_order(client, admin_user)
    response = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    assert response.status_code == 200
    response = await client.put(
        f"/orders/{order['id']}/deliver",
        headers=auth_header(booster_user),
    )
    assert response.status_code == 200
    claims = await client.get(
        f"/orders/{order['id']}/claims", headers=auth_header(admin_user)
    )
    claim_id = claims.json()["items"][0]["id"]
    response = await client.put(
        f"/orders/{order['id']}/claims/{claim_id}/review",
        json={"action": "approve"}, headers=auth_header(admin_user),
    )
    assert response.status_code == 200

    response = await client.put(
        f"/admin/orders/{order['id']}/intervene",
        json={"action": "COMPLETED", "reason": "管理员解决争议"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"

    response = await client.get("/wallet", headers=auth_header(booster_user))
    assert response.status_code == 200
    wallet = response.json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("500.00")
    assert Decimal(str(wallet["total_income"])) == Decimal("500.00")

    response = await client.get(
        "/wallet/transactions", headers=auth_header(booster_user)
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_repeated_admin_completion_does_not_double_credit(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
):
    order = await _create_order(client, admin_user)
    response = await client.put(
        f"/orders/{order['id']}/accept",
        headers=auth_header(booster_user),
    )
    assert response.status_code == 200
    response = await client.put(
        f"/orders/{order['id']}/deliver",
        headers=auth_header(booster_user),
    )
    assert response.status_code == 200
    claims = await client.get(
        f"/orders/{order['id']}/claims", headers=auth_header(admin_user)
    )
    claim_id = claims.json()["items"][0]["id"]
    response = await client.put(
        f"/orders/{order['id']}/claims/{claim_id}/review",
        json={"action": "approve"}, headers=auth_header(admin_user),
    )
    assert response.status_code == 200

    payload = {"action": "COMPLETED", "reason": "管理员完结"}
    for _ in range(2):
        response = await client.put(
            f"/admin/orders/{order['id']}/intervene",
            json=payload,
            headers=auth_header(admin_user),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "COMPLETED"

    response = await client.get("/wallet", headers=auth_header(booster_user))
    assert Decimal(str(response.json()["available_balance"])) == Decimal("500.00")
    assert Decimal(str(response.json()["total_income"])) == Decimal("500.00")

    response = await client.get(
        "/wallet/transactions", headers=auth_header(booster_user)
    )
    assert response.json()["total"] == 1


async def test_admin_completion_without_booster_is_safe(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
):
    order = await _create_order(client, admin_user)
    response = await client.put(
        f"/admin/orders/{order['id']}/intervene",
        json={"action": "COMPLETED", "reason": "无接单人，管理员处理"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"


async def test_admin_intervention_cancellation_cleans_active_claims_and_escrow(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
):
    """Intervention cancellation matches normal cancellation cleanup."""
    publisher = await _register_for_cancellation(client, "admin-cancel-pub@example.com", "AdminCancelPub")
    await _adjust_balance_for_cancellation(client, admin_user, publisher, "1000.00")
    order_response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "max_claims": 2,
            "compensation_amount": "50.00",
        },
        headers=auth_header(publisher),
    )
    assert order_response.status_code == 201
    order_id = order_response.json()["id"]

    booster_id = booster_user["user"]["id"]
    booster_result = await db_session.execute(select(User).where(User.id == booster_id))
    booster = booster_result.scalar_one()
    booster.booster_quota = 2
    await db_session.commit()
    await _adjust_balance_for_cancellation(client, admin_user, booster_user, "100.00")

    response = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster_user)
    )
    assert response.status_code == 200
    response = await client.put(
        f"/orders/{order_id}/deliver", headers=auth_header(booster_user)
    )
    assert response.status_code == 200
    claims_response = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(admin_user)
    )
    claim_id = claims_response.json()["items"][0]["id"]
    response = await client.put(
        f"/orders/{order_id}/claims/{claim_id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200

    # A second active claim on the same multi-claim order still owns a scoped hold.
    booster_two = await _register_for_cancellation(
        client, "admin-cancel-boost-two@example.com", "AdminCancelBoostTwo"
    )
    booster_two_result = await db_session.execute(
        select(User).where(User.id == booster_two["user"]["id"])
    )
    booster_two_record = booster_two_result.scalar_one()
    booster_two_record.role = UserRole.BOOSTER
    await db_session.commit()
    # Global quota of 2: booster_two already holds 2 active orders after the
    # two accepts below, so the third accept must be rejected, and after the
    # intervention-cancelled claim releases a slot the accept succeeds again.
    setting_result = await db_session.execute(
        select(DepositSetting).where(DepositSetting.id == 1)
    )
    setting = setting_result.scalar_one_or_none()
    if setting is None:
        setting = DepositSetting(id=1, enabled=False, global_booster_quota=2)
        db_session.add(setting)
    else:
        setting.global_booster_quota = 2
    await db_session.commit()
    await _adjust_balance_for_cancellation(client, admin_user, booster_two, "125.00")
    response = await client.post(
        "/auth/login",
        json={"email": "admin-cancel-boost-two@example.com", "password": "Passw0rd123"},
    )
    assert response.status_code == 200
    booster_two = response.json()

    # Keep a separate active order/hold on the same wallet. Cancellation must
    # release only the target order's scoped DEPOSIT_HOLD.
    unrelated_order = await _create_order(
        client, admin_user, price="25.00", compensation_amount="25.00"
    )
    response = await client.put(
        f"/orders/{unrelated_order['id']}/accept", headers=auth_header(booster_two)
    )
    assert response.status_code == 200
    response = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster_two)
    )
    assert response.status_code == 200

    quota_order = await _create_order(client, admin_user, price="25.00")
    response = await client.put(
        f"/orders/{quota_order['id']}/accept", headers=auth_header(booster_two)
    )
    assert response.status_code == 400

    publisher_wallet_before = await client.get("/wallet", headers=auth_header(publisher))
    assert Decimal(str(publisher_wallet_before.json()["frozen_balance"])) == Decimal("100.00")
    booster_two_wallet_before = await client.get("/wallet", headers=auth_header(booster_two))
    assert Decimal(str(booster_two_wallet_before.json()["frozen_balance"])) == Decimal("75.00")

    payload = {"action": "CANCELLED", "reason": "管理员取消测试"}
    for _ in range(2):
        response = await client.put(
            f"/admin/orders/{order_id}/intervene",
            json=payload,
            headers=auth_header(admin_user),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "CANCELLED"

    claims_result = await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order_id)
    )
    claims = {claim.booster_id: claim for claim in claims_result.scalars().all()}
    assert claims[booster_id].status == ClaimLifecycleStatus.SETTLED
    assert claims[booster_two["user"]["id"]].status == ClaimLifecycleStatus.CANCELLED

    publisher_wallet = (await client.get("/wallet", headers=auth_header(publisher))).json()
    assert Decimal(str(publisher_wallet["available_balance"])) == Decimal("900.00")
    assert Decimal(str(publisher_wallet["frozen_balance"])) == Decimal("0.00")
    booster_two_wallet = (await client.get("/wallet", headers=auth_header(booster_two))).json()
    assert Decimal(str(booster_two_wallet["available_balance"])) == Decimal("100.00")
    assert Decimal(str(booster_two_wallet["frozen_balance"])) == Decimal("25.00")

    # The active claim no longer consumes quota, so it can accept another order.
    next_order = await _create_order(client, admin_user, price="25.00")
    response = await client.put(
        f"/orders/{next_order['id']}/accept", headers=auth_header(booster_two)
    )
    assert response.status_code == 200

    tx_result = await db_session.execute(
        select(WalletTransaction).where(
            WalletTransaction.order_id == order_id,
            WalletTransaction.booster_id == booster_two["user"]["id"],
            WalletTransaction.type.in_(
                (WalletTransactionType.DEPOSIT_RELEASE, WalletTransactionType.DEPOSIT_HOLD)
            ),
        )
    )
    tx_types = [transaction.type for transaction in tx_result.scalars().all()]
    assert tx_types.count(WalletTransactionType.DEPOSIT_HOLD) == 1
    assert tx_types.count(WalletTransactionType.DEPOSIT_RELEASE) == 1

    escrow_result = await db_session.execute(
        select(WalletTransaction).where(
            WalletTransaction.order_id == order_id,
            WalletTransaction.type == WalletTransactionType.ESCROW_RELEASE,
        )
    )
    assert len(list(escrow_result.scalars().all())) == 1


async def _register_for_cancellation(
    client: AsyncClient, email: str, username: str
) -> dict:
    from app.services import captcha_service

    captcha_id, _ = captcha_service.create()
    code, _ = captcha_service._store[captcha_id]
    response = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "Passw0rd123",
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )
    assert response.status_code in (200, 201)
    return response.json()


async def _adjust_balance_for_cancellation(
    client: AsyncClient, admin_user: dict, user: dict, amount: str
) -> None:
    response = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "管理员取消测试充值"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200


async def test_owner_confirmation_still_settles(
    client: AsyncClient,
    booster_user: dict,
    admin_user: dict,
):
    order = await _create_order(client, admin_user)
    for action in ("accept", "deliver"):
        response = await client.put(
            f"/orders/{order['id']}/{action}",
            headers=auth_header(booster_user),
        )
        assert response.status_code == 200

    response = await client.put(
        f"/orders/{order['id']}/confirm",
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"

    response = await client.get("/wallet", headers=auth_header(booster_user))
    assert Decimal(str(response.json()["total_income"])) == Decimal("500.00")
