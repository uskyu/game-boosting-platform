"""Withdrawal lifecycle tests: create -> freeze -> review -> payout + QR codes."""

from decimal import Decimal
from io import BytesIO

from httpx import AsyncClient

from tests.conftest import auth_header

# PNG magic + >1MB payload (magic check only inspects the header bytes)
BIG_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * (1024 * 1024 + 128)


async def _fund_wallet(
    client: AsyncClient, admin_user: dict, user_id: int, amount: str
) -> None:
    resp = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": amount, "reason": "测试入账"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200


async def _create_withdrawal(
    client: AsyncClient, user_data: dict, amount: str = "40.00"
) -> dict:
    resp = await client.post(
        "/withdrawals",
        json={
            "amount": amount,
            "channel": "ALIPAY",
            "account_name": "张三",
            "account_no": "13800000000",
        },
        headers=auth_header(user_data),
    )
    return resp


async def test_withdraw_insufficient_balance_rejected(
    client: AsyncClient, registered_user: dict
):
    """Withdrawing more than available balance is rejected."""
    resp = await _create_withdrawal(client, registered_user, "50.00")
    assert resp.status_code == 400

    # Nothing was persisted
    resp = await client.get("/withdrawals/mine", headers=auth_header(registered_user))
    assert resp.json()["total"] == 0


async def test_withdraw_below_minimum_rejected(
    client: AsyncClient, registered_user: dict
):
    resp = await _create_withdrawal(client, registered_user, "0.50")
    assert resp.status_code == 422  # schema requires amount >= 1


async def test_withdraw_freezes_balance(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "100.00")

    resp = await _create_withdrawal(client, registered_user, "40.00")
    assert resp.status_code == 201
    withdrawal = resp.json()
    assert withdrawal["status"] == "PENDING"
    assert Decimal(str(withdrawal["amount"])) == Decimal("40.00")
    assert withdrawal["channel"] == "ALIPAY"

    # Wallet: available decreased, frozen increased
    resp = await client.get("/wallet", headers=auth_header(registered_user))
    wallet = resp.json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("60.00")
    assert Decimal(str(wallet["frozen_balance"])) == Decimal("40.00")

    # Freeze ledger entry written with negative amount
    resp = await client.get("/wallet/transactions", headers=auth_header(registered_user))
    items = resp.json()["items"]
    freeze_txs = [tx for tx in items if tx["type"] == "WITHDRAWAL_FREEZE"]
    assert len(freeze_txs) == 1
    assert Decimal(str(freeze_txs[0]["amount"])) == Decimal("-40.00")
    assert freeze_txs[0]["withdrawal_id"] == withdrawal["id"]
    assert Decimal(str(freeze_txs[0]["balance_after"])) == Decimal("60.00")

    # Mine listing shows the record
    resp = await client.get("/withdrawals/mine", headers=auth_header(registered_user))
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_reject_withdrawal_refunds_frozen(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "100.00")
    resp = await _create_withdrawal(client, registered_user, "40.00")
    assert resp.status_code == 201
    wid = resp.json()["id"]

    # Reject without reason -> 400
    resp = await client.post(
        f"/admin/withdrawals/{wid}/review",
        json={"action": "reject"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400

    # Reject with reason -> REJECTED + refund
    resp = await client.post(
        f"/admin/withdrawals/{wid}/review",
        json={"action": "reject", "reason": "账号信息有误"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REJECTED"
    assert data["reject_reason"] == "账号信息有误"
    assert data["reviewed_by"] == admin_user["user"]["id"]
    assert data["reviewed_at"] is not None

    # Balance fully restored, frozen cleared
    resp = await client.get("/wallet", headers=auth_header(registered_user))
    wallet = resp.json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("100.00")
    assert Decimal(str(wallet["frozen_balance"])) == Decimal("0.00")

    # Refund ledger entry exists
    resp = await client.get("/wallet/transactions", headers=auth_header(registered_user))
    refund_txs = [
        tx for tx in resp.json()["items"] if tx["type"] == "WITHDRAWAL_REFUND"
    ]
    assert len(refund_txs) == 1
    assert Decimal(str(refund_txs[0]["amount"])) == Decimal("40.00")
    assert refund_txs[0]["withdrawal_id"] == wid

    # Re-reviewing a rejected withdrawal is rejected
    resp = await client.post(
        f"/admin/withdrawals/{wid}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400


async def test_approve_then_mark_paid_full_chain(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    """approve -> mark-paid clears frozen balance and accumulates total_withdrawn."""
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "100.00")
    resp = await _create_withdrawal(client, registered_user, "40.00")
    wid = resp.json()["id"]

    # mark-paid before review -> 400 (only APPROVED can be paid)
    resp = await client.post(
        f"/admin/withdrawals/{wid}/mark-paid",
        json={"payment_reference": "TX123"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400

    # Approve
    resp = await client.post(
        f"/admin/withdrawals/{wid}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "APPROVED"
    assert data["reviewed_by"] == admin_user["user"]["id"]
    assert data["reviewed_at"] is not None

    # Approve again -> 400 (no longer PENDING)
    resp = await client.post(
        f"/admin/withdrawals/{wid}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400

    # After approve: available still 60, frozen still 40
    resp = await client.get("/wallet", headers=auth_header(registered_user))
    wallet = resp.json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("60.00")
    assert Decimal(str(wallet["frozen_balance"])) == Decimal("40.00")

    # Mark paid
    resp = await client.post(
        f"/admin/withdrawals/{wid}/mark-paid",
        json={"payment_reference": "TX-2026-0001"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PAID"
    assert data["payment_reference"] == "TX-2026-0001"
    assert data["paid_by"] == admin_user["user"]["id"]
    assert data["paid_at"] is not None

    # Frozen cleared, total_withdrawn accumulated
    resp = await client.get("/wallet", headers=auth_header(registered_user))
    wallet = resp.json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("60.00")
    assert Decimal(str(wallet["frozen_balance"])) == Decimal("0.00")
    assert Decimal(str(wallet["total_withdrawn"])) == Decimal("40.00")

    # Paid ledger entry exists with negative amount
    resp = await client.get("/wallet/transactions", headers=auth_header(registered_user))
    paid_txs = [tx for tx in resp.json()["items"] if tx["type"] == "WITHDRAWAL_PAID"]
    assert len(paid_txs) == 1
    assert Decimal(str(paid_txs[0]["amount"])) == Decimal("-40.00")
    assert paid_txs[0]["withdrawal_id"] == wid

    # mark-paid twice -> 400
    resp = await client.post(
        f"/admin/withdrawals/{wid}/mark-paid",
        json={"payment_reference": "TX-2026-0002"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400


async def test_admin_withdrawal_list_has_username(
    client: AsyncClient, registered_user: dict, admin_user: dict
):
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "10.00")
    resp = await _create_withdrawal(client, registered_user, "5.00")
    assert resp.status_code == 201

    resp = await client.get(
        "/admin/withdrawals?status=PENDING", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["username"] == registered_user["user"]["username"]
    assert item["user_id"] == user_id

    # Status filter with no match returns empty
    resp = await client.get(
        "/admin/withdrawals?status=PAID", headers=auth_header(admin_user)
    )
    assert resp.json()["total"] == 0


async def test_withdrawal_endpoints_require_auth(client: AsyncClient):
    resp = await client.post(
        "/withdrawals",
        json={
            "amount": "10.00",
            "channel": "ALIPAY",
            "account_name": "张三",
            "account_no": "13800000000",
        },
    )
    assert resp.status_code == 401

    resp = await client.get("/withdrawals/mine")
    assert resp.status_code == 401


async def test_admin_withdrawal_actions_require_admin(
    client: AsyncClient, registered_user: dict, admin_user: dict, booster_user: dict
):
    """A regular (non-admin) user cannot call admin withdrawal endpoints."""
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "100.00")
    resp = await _create_withdrawal(client, registered_user, "40.00")
    wid = resp.json()["id"]

    for method, url, payload in (
        ("post", f"/admin/withdrawals/{wid}/review", {"action": "approve"}),
        ("post", f"/admin/withdrawals/{wid}/mark-paid", {"payment_reference": "X"}),
        ("get", "/admin/withdrawals", None),
    ):
        if method == "post":
            resp = await client.post(url, json=payload, headers=auth_header(booster_user))
        else:
            resp = await client.get(url, headers=auth_header(booster_user))
        assert resp.status_code == 403
