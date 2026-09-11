"""Focused regressions for high-risk order settlement paths."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select

from app.models.order import ClaimLifecycleStatus, Order, OrderClaim
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from app.services.order_service import get_order_service
from app.services.payout_scheduler import scan_due_payouts
from app.services.wallet_service import get_wallet_service
from tests.conftest import auth_header


async def _enable_deposit(client: AsyncClient, admin_user: dict, **overrides):
    current = (
        await client.get(
            "/admin/deposit/settings", headers=auth_header(admin_user)
        )
    ).json()
    body = {
        "enabled": True,
        "return_cooldown_days": current["return_cooldown_days"],
        "default_compensation": current.get("default_compensation", 20),
        "settlement_mode": current.get("settlement_mode", "AFTER_DELIVERY"),
        "tiers": [
            {
                "threshold": tier["threshold"],
                "wait_seconds": tier["wait_seconds"],
                "exempt_compensation": tier["exempt_compensation"],
                "settle_hours": tier["settle_hours"],
                "enabled": tier["enabled"],
            }
            for tier in current["tiers"]
        ],
    }
    body.update(overrides)
    response = await client.put(
        "/admin/deposit/settings",
        json=body,
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: str):
    response = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "high-risk regression"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text


async def _create_order(
    client: AsyncClient,
    admin_user: dict,
    *,
    price: str = "100.00",
    compensation: str | None = None,
    payout_delay_hours: int | None = None,
) -> dict:
    payload = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": price,
    }
    if compensation is not None:
        payload["compensation_amount"] = compensation
    if payout_delay_hours is not None:
        payload["payout_delay_hours"] = payout_delay_hours
    response = await client.post(
        "/orders/create", json=payload, headers=auth_header(admin_user)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _claims(db_session, order_id: int) -> list[OrderClaim]:
    result = await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order_id)
    )
    return list(result.scalars().all())


async def test_admin_assignment_holds_compensation_atomically(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    await _enable_deposit(
        client,
        admin_user,
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": False,
                "settle_hours": 72,
                "enabled": True,
            }
        ],
    )
    order = await _create_order(
        client, admin_user, compensation="20.00"
    )

    failed = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": booster_user["user"]["id"]},
        headers=auth_header(admin_user),
    )
    assert failed.status_code == 400
    assert "赔偿金" in failed.json()["detail"]

    await db_session.rollback()
    order_row = (
        await db_session.execute(select(Order).where(Order.id == order["id"]))
    ).scalar_one()
    assert order_row.status.value == "PENDING"
    assert order_row.booster_id is None
    assert await _claims(db_session, order["id"]) == []

    await _fund(client, admin_user, booster_user, "100.00")
    assigned = await client.put(
        f"/admin/orders/{order['id']}/assign",
        json={"booster_id": booster_user["user"]["id"]},
        headers=auth_header(admin_user),
    )
    assert assigned.status_code == 200, assigned.text

    await db_session.rollback()
    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    assert Decimal(str(wallet.available_balance)) == Decimal("80.00")
    assert Decimal(str(wallet.frozen_balance)) == Decimal("20.00")
    claims = await _claims(db_session, order["id"])
    assert len(claims) == 1
    assert claims[0].status == ClaimLifecycleStatus.CLAIMED


async def test_confirm_cannot_bypass_after_approval_review(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    await _enable_deposit(
        client,
        admin_user,
        settlement_mode="AFTER_APPROVAL",
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": True,
                "settle_hours": 24,
                "enabled": True,
            }
        ],
    )
    order = await _create_order(client, admin_user, compensation="20.00")
    assert (
        await client.put(
            f"/orders/{order['id']}/accept",
            headers=auth_header(booster_user),
        )
    ).status_code == 200
    assert (
        await client.put(
            f"/orders/{order['id']}/deliver",
            headers=auth_header(booster_user),
        )
    ).status_code == 200

    response = await client.put(
        f"/orders/{order['id']}/confirm",
        headers=auth_header(admin_user),
    )
    assert response.status_code == 400
    assert "先逐个审核" in response.json()["detail"]

    await db_session.rollback()
    claim = (await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order["id"])
    )).scalar_one()
    assert claim.status == ClaimLifecycleStatus.DELIVERED
    income = (await db_session.execute(
        select(WalletTransaction).where(
            WalletTransaction.order_id == order["id"],
            WalletTransaction.type == WalletTransactionType.ORDER_INCOME,
        )
    )).scalars().all()
    assert income == []


async def test_after_approval_omitted_amount_is_snapshotted_and_used(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    await _enable_deposit(
        client,
        admin_user,
        settlement_mode="AFTER_APPROVAL",
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": True,
                "settle_hours": 24,
                "enabled": True,
            }
        ],
    )
    order = await _create_order(client, admin_user, price="100.00")
    assert (
        await client.put(
            f"/orders/{order['id']}/accept",
            headers=auth_header(booster_user),
        )
    ).status_code == 200
    assert (
        await client.put(
            f"/orders/{order['id']}/deliver",
            headers=auth_header(booster_user),
        )
    ).status_code == 200
    claim = (await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order["id"])
    )).scalar_one()

    reviewed = await client.put(
        f"/orders/{order['id']}/claims/{claim.id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["approved_payout_amount"] == "100.00"

    await db_session.commit()
    await db_session.refresh(claim)
    claim.settlement_due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    order_row = (await db_session.execute(
        select(Order).where(Order.id == order["id"])
    )).scalar_one()
    order_row.price = Decimal("999.00")
    await db_session.commit()

    settled = await scan_due_payouts(db_session)
    await db_session.commit()
    assert claim.id in settled
    income = (await db_session.execute(
        select(WalletTransaction).where(
            WalletTransaction.order_id == order["id"],
            WalletTransaction.booster_id == booster_user["user"]["id"],
            WalletTransaction.type == WalletTransactionType.ORDER_INCOME,
        )
    )).scalar_one()
    assert Decimal(str(income.amount)) == Decimal("100.00")


async def test_scheduler_rechecks_claim_status_after_lock(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session, monkeypatch
):
    await _enable_deposit(
        client,
        admin_user,
        settlement_mode="AFTER_DELIVERY",
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": True,
                "settle_hours": 1,
                "enabled": True,
            }
        ],
    )
    order = await _create_order(client, admin_user)
    await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(booster_user)
    )
    await client.put(
        f"/orders/{order['id']}/deliver", headers=auth_header(booster_user)
    )
    claim = (await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order["id"])
    )).scalar_one()
    claim.settlement_due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    real_session = db_session
    claim_id = claim.id

    class RaceSession:
        def __init__(self):
            self.claim_lock_seen = False

        def __getattr__(self, name):
            return getattr(real_session, name)

        def begin_nested(self):
            return real_session.begin_nested()

        async def execute(self, statement, *args, **kwargs):
            result = await real_session.execute(statement, *args, **kwargs)
            if (
                getattr(statement, "_for_update_arg", None) is not None
                and "order_claims.id =" in str(statement)
            ):
                self.claim_lock_seen = True
                raced = (await real_session.execute(
                    select(OrderClaim).where(OrderClaim.id == claim_id)
                )).scalar_one()
                raced.status = ClaimLifecycleStatus.SETTLED
                await real_session.flush()
            return result

    race_session = RaceSession()
    assert await scan_due_payouts(race_session) == []
    assert race_session.claim_lock_seen is True
    await db_session.rollback()
    claim = (await db_session.execute(
        select(OrderClaim).where(OrderClaim.id == claim_id)
    )).scalar_one()
    assert claim.status == ClaimLifecycleStatus.DELIVERED
    income = (await db_session.execute(
        select(WalletTransaction).where(
            WalletTransaction.order_id == order["id"],
            WalletTransaction.type == WalletTransactionType.ORDER_INCOME,
        )
    )).scalars().all()
    assert income == []


async def test_legacy_null_snapshot_uses_order_delay_when_deposit_disabled(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    order = await _create_order(
        client,
        admin_user,
        payout_delay_hours=1,
    )
    await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(booster_user)
    )
    await client.put(
        f"/orders/{order['id']}/deliver", headers=auth_header(booster_user)
    )
    claim = (await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order["id"])
    )).scalar_one()
    claim.settlement_mode_snapshot = None
    claim.settle_hours_snapshot = None
    claim.settlement_due_at = None
    claim.delivered_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.commit()

    settled = await scan_due_payouts(db_session)
    await db_session.commit()
    assert claim.id in settled


async def test_post_033_disabled_claim_uses_explicit_order_delay_marker(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    order = await _create_order(client, admin_user, payout_delay_hours=1)
    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))
    await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))
    claim = (await db_session.execute(select(OrderClaim).where(OrderClaim.order_id == order["id"]))).scalar_one()
    assert claim.settlement_mode_snapshot == "ORDER_DELAY"
    assert claim.settle_hours_snapshot is None
    assert claim.settlement_due_at is None


async def test_post_033_no_tier_claim_uses_explicit_order_delay_marker(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    await _enable_deposit(client, admin_user, tiers=[{"threshold": 0, "wait_seconds": 0, "exempt_compensation": True, "settle_hours": 1, "enabled": False}])
    await _fund(client, admin_user, booster_user, "100.00")
    order = await _create_order(client, admin_user, payout_delay_hours=1)
    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))
    await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))
    claim = (await db_session.execute(select(OrderClaim).where(OrderClaim.order_id == order["id"]))).scalar_one()
    assert claim.settlement_mode_snapshot == "ORDER_DELAY"


async def test_legacy_deposit_tier_wins_over_order_delay(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    await _enable_deposit(client, admin_user, settlement_mode="AFTER_DELIVERY", tiers=[{"threshold": 0, "wait_seconds": 0, "exempt_compensation": True, "settle_hours": 1, "enabled": True}])
    order = await _create_order(client, admin_user, payout_delay_hours=23)
    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))
    await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))
    claim = (await db_session.execute(select(OrderClaim).where(OrderClaim.order_id == order["id"]))).scalar_one()
    claim.settlement_mode_snapshot = None
    claim.settle_hours_snapshot = None
    claim.settlement_due_at = None
    claim.delivered_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.commit()
    assert claim.id in await scan_due_payouts(db_session)


async def test_legacy_after_approval_uses_approved_at_for_tier_due(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    await _enable_deposit(client, admin_user, settlement_mode="AFTER_APPROVAL", tiers=[{"threshold": 0, "wait_seconds": 0, "exempt_compensation": True, "settle_hours": 1, "enabled": True}])
    order = await _create_order(client, admin_user, payout_delay_hours=23)
    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))
    await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))
    claim = (await db_session.execute(select(OrderClaim).where(OrderClaim.order_id == order["id"]))).scalar_one()
    claim.settlement_mode_snapshot = None
    claim.settle_hours_snapshot = None
    claim.settlement_due_at = None
    claim.approved_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.commit()
    assert claim.id in await scan_due_payouts(db_session)


async def test_confirm_rejects_approved_after_approval_before_due(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    await _enable_deposit(client, admin_user, settlement_mode="AFTER_APPROVAL", tiers=[{"threshold": 0, "wait_seconds": 0, "exempt_compensation": True, "settle_hours": 24, "enabled": True}])
    order = await _create_order(client, admin_user)
    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))
    await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))
    claim = (await db_session.execute(select(OrderClaim).where(OrderClaim.order_id == order["id"]))).scalar_one()
    response = await client.put(f"/orders/{order['id']}/claims/{claim.id}/review", json={"action": "approve"}, headers=auth_header(admin_user))
    assert response.status_code == 200
    response = await client.put(f"/orders/{order['id']}/confirm", headers=auth_header(admin_user))
    assert response.status_code == 400
    assert "固定结算时间" in response.json()["detail"]


async def test_admin_force_completion_rejects_active_claim(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    order = await _create_order(client, admin_user)
    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))
    response = await client.put(f"/admin/orders/{order['id']}/intervene", json={"action": "COMPLETED", "reason": "force"}, headers=auth_header(admin_user))
    assert response.status_code == 400
    assert "未完成" in response.json()["detail"]
    row = (await db_session.execute(select(Order).where(Order.id == order["id"]))).scalar_one()
    assert row.status.value == "LOCKED"


async def test_cancelled_claim_old_hold_is_repaired_once(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    await _enable_deposit(client, admin_user, tiers=[{"threshold": 0, "wait_seconds": 0, "exempt_compensation": False, "settle_hours": 1, "enabled": True}])
    await _fund(client, admin_user, booster_user, "100.00")
    order = await _create_order(client, admin_user, compensation="20.00")
    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))
    claim = (await db_session.execute(select(OrderClaim).where(OrderClaim.order_id == order["id"]))).scalar_one()
    claim.status = ClaimLifecycleStatus.CANCELLED
    await db_session.commit()
    for _ in range(2):
        response = await client.put(f"/admin/orders/{order['id']}/intervene", json={"action": "CANCELLED"}, headers=auth_header(admin_user))
        assert response.status_code == 200
    txs = (await db_session.execute(select(WalletTransaction).where(WalletTransaction.order_id == order["id"], WalletTransaction.booster_id == booster_user["user"]["id"], WalletTransaction.type == WalletTransactionType.DEPOSIT_RELEASE))).scalars().all()
    assert len(txs) == 1


async def test_legacy_deduct_deposit_rejects_unscoped_write(db_session, booster_user: dict):
    service = get_wallet_service(db_session)
    wallet = await service.get_or_create_wallet(booster_user["user"]["id"])
    with pytest.raises(HTTPException) as exc:
        await service.deduct_deposit(wallet, amount=Decimal("1.00"))
    assert exc.value.status_code == 400
    assert "关联订单和打手名额" in exc.value.detail


async def test_pending_review_count_excludes_approved_delivered_claim(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    await _enable_deposit(
        client,
        admin_user,
        settlement_mode="AFTER_APPROVAL",
        tiers=[{"threshold": 0, "wait_seconds": 0, "exempt_compensation": True, "settle_hours": 24, "enabled": True}],
    )
    order = await _create_order(client, admin_user)
    assert (await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))).status_code == 200
    assert (await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))).status_code == 200
    claim = (await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order["id"])
    )).scalar_one()
    review = await client.put(
        f"/orders/{order['id']}/claims/{claim.id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert review.status_code == 200
    assert review.json()["status"] == "DELIVERED"
    # The API request uses a separate transaction; reset this direct session
    # before reading the committed approval timestamp on MySQL.
    await db_session.rollback()
    raw_counts = await get_order_service(db_session).claim_status_counts([order["id"]])
    assert raw_counts[order["id"]]["DELIVERED"] == 1
    assert raw_counts[order["id"]].get("PENDING_REVIEW", 0) == 0

    detail = await client.get(f"/orders/{order['id']}", headers=auth_header(admin_user))
    assert detail.status_code == 200
    assert detail.json()["pending_review_count"] == 0
    assert detail.json()["settled_count"] == 0

    listing = await client.get("/admin/orders?page=1&page_size=100", headers=auth_header(admin_user))
    assert listing.status_code == 200
    listed = {item["id"]: item for item in listing.json()["items"]}
    assert listed[order["id"]]["pending_review_count"] == 0
