"""Wallet settlement and admin adjust tests."""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select

from app.models.order import Order
from app.models.wallet import Wallet
from tests.conftest import auth_header


async def _full_completed_order(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    price: str = "500.00",
) -> dict:
    """Helper: boss creates order, booster accepts + delivers, boss confirms."""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()

    resp = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(booster_user)
    )
    assert resp.status_code == 200
    resp = await client.put(
        f"/orders/{order['id']}/deliver", headers=auth_header(booster_user)
    )
    assert resp.status_code == 200
    resp = await client.put(
        f"/orders/{order['id']}/confirm", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"
    return order


async def test_confirm_order_credits_booster_wallet(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """Order completion credits the booster wallet with price - commission."""
    order = await _full_completed_order(client, admin_user, booster_user, "500.00")

    resp = await client.get("/wallet", headers=auth_header(booster_user))
    assert resp.status_code == 200
    wallet = resp.json()
    # COMMISSION_RATE defaults to 0.0 -> full price credited
    assert Decimal(str(wallet["available_balance"])) == Decimal("500.00")
    assert Decimal(str(wallet["frozen_balance"])) == Decimal("0.00")
    assert Decimal(str(wallet["total_income"])) == Decimal("500.00")
    assert Decimal(str(wallet["total_withdrawn"])) == Decimal("0.00")

    # Ledger contains exactly one ORDER_INCOME entry with correct fields
    resp = await client.get("/wallet/transactions", headers=auth_header(booster_user))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    tx = data["items"][0]
    assert tx["type"] == "ORDER_INCOME"
    assert Decimal(str(tx["amount"])) == Decimal("500.00")
    assert Decimal(str(tx["balance_before"])) == Decimal("0.00")
    assert Decimal(str(tx["balance_after"])) == Decimal("500.00")
    assert tx["order_id"] == order["id"]


async def test_duplicate_settle_does_not_double_credit(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
):
    """Re-settling the same order must be a no-op (idempotent)."""
    order = await _full_completed_order(client, admin_user, booster_user, "500.00")

    # Call the settlement service again against the same order
    from app.services.wallet_service import get_wallet_service

    result = await db_session.execute(select(Order).where(Order.id == order["id"]))
    order_obj = result.scalar_one()

    wallet_service = get_wallet_service(db_session)
    tx = await wallet_service.settle_order_income(order_obj)
    assert tx is None  # already settled

    # Also a second confirm attempt through the API must be rejected
    resp = await client.put(
        f"/orders/{order['id']}/confirm", headers=auth_header(admin_user)
    )
    assert resp.status_code == 400

    # Wallet still holds a single credit
    resp = await client.get("/wallet", headers=auth_header(booster_user))
    wallet = resp.json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("500.00")
    assert Decimal(str(wallet["total_income"])) == Decimal("500.00")

    resp = await client.get("/wallet/transactions", headers=auth_header(booster_user))
    assert resp.json()["total"] == 1


async def test_admin_adjust_positive_and_negative(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    """Admin can add and deduct balance; negative results and zero are rejected."""
    user_id = registered_user["user"]["id"]

    resp = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": "100.00", "reason": "活动奖励"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    wallet = resp.json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("100.00")

    resp = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": "-30.50", "reason": "纠错扣减"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    wallet = resp.json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("69.50")

    # Ledger records both signed adjustments
    resp = await client.get("/wallet/transactions", headers=auth_header(registered_user))
    items = resp.json()["items"]
    assert len(items) == 2
    types = {tx["type"] for tx in items}
    assert types == {"ADMIN_ADJUST"}
    amounts = sorted(Decimal(str(tx["amount"])) for tx in items)
    assert amounts == [Decimal("-30.50"), Decimal("100.00")]

    # Deducting below zero is rejected
    resp = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": "-500.00", "reason": "超额扣减"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400

    # Zero adjustment is rejected
    resp = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": "0", "reason": "零调整"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400

    # Balance untouched by the failed adjustments
    resp = await client.get("/wallet", headers=auth_header(registered_user))
    assert Decimal(str(resp.json()["available_balance"])) == Decimal("69.50")


async def test_adjust_nonexistent_user_404(client: AsyncClient, admin_user: dict):
    resp = await client.post(
        "/admin/wallets/999999/adjust",
        json={"amount": "10.00", "reason": "测试"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 404


async def test_admin_endpoints_require_admin(
    client: AsyncClient, registered_user: dict
):
    """Regular users get 403 on admin wallet endpoints."""
    user_id = registered_user["user"]["id"]
    resp = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": "10.00", "reason": "越权"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403

    resp = await client.get(
        "/admin/withdrawals", headers=auth_header(registered_user)
    )
    assert resp.status_code == 403


async def test_wallet_requires_auth(client: AsyncClient):
    resp = await client.get("/wallet")
    assert resp.status_code == 401

    resp = await client.get("/wallet/transactions")
    assert resp.status_code == 401


async def test_wallet_autocreated_for_new_user(
    client: AsyncClient, registered_user: dict, db_session
):
    """First wallet access lazily creates a zero-balance wallet row."""
    resp = await client.get("/wallet", headers=auth_header(registered_user))
    assert resp.status_code == 200
    wallet = resp.json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("0.00")

    result = await db_session.execute(
        select(Wallet).where(Wallet.user_id == registered_user["user"]["id"])
    )
    assert result.scalar_one_or_none() is not None

    # No transactions yet
    resp = await client.get("/wallet/transactions", headers=auth_header(registered_user))
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
