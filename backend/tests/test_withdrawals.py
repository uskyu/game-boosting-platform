"""Withdrawal lifecycle tests: create -> freeze -> review -> payout + QR codes."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.product_time import PRODUCT_TIMEZONE
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


# =============================================================================
# 提现机会刷新规则（后台可配）+ 提现金额必须为整数
# =============================================================================


async def _register(
    client: AsyncClient, make_captcha, email: str, username: str
) -> dict:
    """注册一个新用户并返回登录态数据。"""
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "TestPass123",
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201)
    return resp.json()


async def _set_rule(
    client: AsyncClient,
    admin_user: dict,
    *,
    mode: str,
    interval_hours: int | None = None,
) -> dict:
    payload: dict = {"mode": mode}
    if interval_hours is not None:
        payload["interval_hours"] = interval_hours
    resp = await client.put(
        "/admin/withdrawal-rule/settings",
        json=payload,
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    return resp.json()


async def _reset_withdrawal_created_at(
    db_session: AsyncSession, *, user_id: int, moment: datetime
) -> None:
    """直改该用户所有未驳回提现的 created_at（值是 naive UTC）。"""
    await db_session.execute(
        text(
            "UPDATE withdrawal_requests SET created_at = :moment "
            "WHERE user_id = :user_id AND status != 'REJECTED'"
        ),
        {"moment": moment, "user_id": user_id},
    )
    await db_session.commit()


def _utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _product_local_moment(
    year: int, month: int, day: int, hour: int, minute: int = 0
) -> datetime:
    """产品时区（+08:00）某一时刻对应的 naive UTC。"""
    return (
        datetime(year, month, day, hour, minute, tzinfo=PRODUCT_TIMEZONE)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def test_withdrawal_amount_must_be_whole_yuan(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
    make_captcha,
):
    """提现金额只能是整数：50.5 被 422 拦下，字符串/整数形式的整数元都受理。"""
    user_id = registered_user["user"]["id"]
    other = await _register(client, make_captcha, "int_amount@example.com", "IntAmount")
    await _fund_wallet(client, admin_user, user_id, "200.00")
    await _fund_wallet(client, admin_user, other["user"]["id"], "200.00")

    # 非整数在 schema 层就被拒（ge=1 拦不住 50.5，靠整数校验）
    resp = await _create_withdrawal(client, registered_user, "50.5")
    assert resp.status_code == 422
    assert "整数" in resp.text

    # 字符串 "50" 与整数 100 都算整数元
    resp = await _create_withdrawal(client, registered_user, "50")
    assert resp.status_code == 201

    resp = await client.post(
        "/withdrawals",
        json={
            "amount": 100,
            "channel": "ALIPAY",
            "account_name": "张三",
            "account_no": "13800000000",
        },
        headers=auth_header(other),
    )
    assert resp.status_code == 201

    # 库里落库按两位小数存成 50.00
    resp = await client.get("/withdrawals/mine", headers=auth_header(registered_user))
    assert resp.json()["items"][0]["amount"] == "50.00"
    assert resp.json()["total"] == 1


async def test_interval_mode_rolling_window_per_user(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
    db_session: AsyncSession,
):
    """模式 A：每人每次提现成功后过 N 小时才恢复 1 次机会。"""
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "200.00")

    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 201

    # 同一周期内第二次直接被拒
    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 400
    assert "提现机会" in resp.json()["detail"]

    # 把最近一条提现改到 25 小时前 → 已过刷新间隔，可再提
    await _reset_withdrawal_created_at(
        db_session, user_id=user_id, moment=_utc_naive() - timedelta(hours=25)
    )
    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 201

    # 再改回 23 小时前 → 还没到 24 小时，仍然拒绝
    await _reset_withdrawal_created_at(
        db_session, user_id=user_id, moment=_utc_naive() - timedelta(hours=23)
    )
    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 400


async def test_daily_noon_mode_site_wide_window(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
    db_session: AsyncSession,
):
    """模式 B：全站统一 [今天 12:00, 明天 12:00) 窗口，窗口内提过就没机会。"""
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "200.00")
    await _set_rule(client, admin_user, mode="DAILY_NOON")

    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 201

    # 当前所在的 12 点窗口按运行时刻现算（凌晨跑测试时窗口起点是昨天 12:00），
    # 断言挂在窗口边界上而不是写死“今天 11 点”，避免用例随运行时刻变红。
    now = _utc_naive()
    today_local = now.astimezone(PRODUCT_TIMEZONE).date()
    noon_today = _product_local_moment(
        today_local.year, today_local.month, today_local.day, 12
    )
    window_start = noon_today if now >= noon_today else noon_today - timedelta(days=1)

    # 上一窗口（窗口起点前 1 分钟）→ 新窗口已刷新，可提
    await _reset_withdrawal_created_at(
        db_session, user_id=user_id, moment=window_start - timedelta(minutes=1)
    )
    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 201

    # 当前窗口内（窗口起点后 1 分钟）→ 机会已用完
    await _reset_withdrawal_created_at(
        db_session, user_id=user_id, moment=window_start + timedelta(minutes=1)
    )
    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 400
    assert "提现机会" in resp.json()["detail"]

    # 再往前一个窗口（25 小时前）→ 同样放行
    await _reset_withdrawal_created_at(
        db_session, user_id=user_id, moment=window_start - timedelta(hours=25)
    )
    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 201


async def test_rule_change_takes_effect_immediately(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
    db_session: AsyncSession,
):
    """改规则即时生效：模式 A 下刚提过，切成模式 B 后同一用户立刻受限。"""
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "200.00")

    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 201

    today = _utc_naive().astimezone(PRODUCT_TIMEZONE).date()
    await _reset_withdrawal_created_at(
        db_session,
        user_id=user_id,
        moment=_product_local_moment(today.year, today.month, today.day, 13),
    )

    # 模式 A 下这条 created_at 是「刚才」，本来还能再提
    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 400  # 24 小时未到

    await _set_rule(client, admin_user, mode="DAILY_NOON")

    # 切到模式 B 后，13:00 落在今天窗口内 → 立刻按新规则拒绝
    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 400
    assert "提现机会" in resp.json()["detail"]


async def test_rejected_withdrawal_does_not_consume_quota(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
):
    """被驳回的提现不占机会：驳回后额度恢复，可再次申请。"""
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "200.00")

    resp = await _create_withdrawal(client, registered_user, "40.00")
    assert resp.status_code == 201
    wid = resp.json()["id"]

    resp = await client.post(
        f"/admin/withdrawals/{wid}/review",
        json={"action": "reject", "reason": "账号信息有误"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200

    resp = await _create_withdrawal(client, registered_user, "40.00")
    assert resp.status_code == 201


async def test_withdrawal_rule_settings_validation_and_permissions(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
):
    """后台规则设置的取值范围、权限与默认值。"""
    # 全新库默认：模式 A，24 小时
    resp = await client.get(
        "/admin/withdrawal-rule/settings", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "INTERVAL"
    assert data["interval_hours"] == 24
    assert data["updated_at"]

    # 间隔必须落在 [1, 168]
    for bad in (0, 169, -1):
        resp = await client.put(
            "/admin/withdrawal-rule/settings",
            json={"mode": "INTERVAL", "interval_hours": bad},
            headers=auth_header(admin_user),
        )
        assert resp.status_code == 422, bad

    # 保存合法值
    resp = await client.put(
        "/admin/withdrawal-rule/settings",
        json={"mode": "INTERVAL", "interval_hours": 6},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["interval_hours"] == 6

    # 普通用户既不能读也不能写
    resp = await client.get(
        "/admin/withdrawal-rule/settings", headers=auth_header(registered_user)
    )
    assert resp.status_code == 403
    resp = await client.put(
        "/admin/withdrawal-rule/settings",
        json={"mode": "DAILY_NOON"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403


async def test_withdrawal_quota_endpoint(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
):
    """GET /withdrawals/quota：提现一次后机会用完，并给出下次刷新时间。"""
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "200.00")

    resp = await client.get("/withdrawals/quota", headers=auth_header(registered_user))
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["mode"] == "INTERVAL"
    assert data["interval_hours"] == 24
    assert data["next_refresh_at"] is None

    resp = await _create_withdrawal(client, registered_user, "10")
    assert resp.status_code == 201
    created_at = resp.json()["created_at"]

    resp = await client.get("/withdrawals/quota", headers=auth_header(registered_user))
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["next_refresh_at"] is not None
    assert _parse_utc(data["next_refresh_at"]) == _parse_utc(created_at) + timedelta(
        hours=24
    )

