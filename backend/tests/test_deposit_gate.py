"""保证金玩法测试：接单等待闸门、免炸单赔付金、结账时效两种计时模式。

覆盖重点：
- 保证金模式关闭时完全不干预接单（无等待、不填充默认赔付金）；
- 开启后所有订单按档位施加接单等待，顶档可立即接单；
- 订单列表/详情把等待信息下发给前端；
- 档位免除赔付金的打手接单不冻结，未达标的仍冻结；
- AFTER_DELIVERY：交付后到期自动结算；
- AFTER_APPROVAL：审核通过只记录时间，到期后才自动放款。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select

from app.models.order import ClaimLifecycleStatus, Order, OrderClaim
from app.models.wallet import Wallet
from app.services.payout_scheduler import scan_due_payouts
from tests.conftest import auth_header

DEPOSIT_SETTINGS = "/admin/deposit/settings"
MY_DEPOSIT = "/wallet/deposit"


async def _enable_deposit(client: AsyncClient, admin_user: dict, **overrides):
    payload = (await client.get(DEPOSIT_SETTINGS, headers=auth_header(admin_user))).json()
    body = {
        "enabled": True,
        "return_cooldown_days": payload["return_cooldown_days"],
        "default_compensation": payload.get("default_compensation", 20),
        "settlement_mode": payload.get("settlement_mode", "AFTER_DELIVERY"),
        "tiers": [
            {
                "threshold": t["threshold"],
                "wait_seconds": t["wait_seconds"],
                "exempt_compensation": t["exempt_compensation"],
                "settle_hours": t["settle_hours"],
                "enabled": t["enabled"],
            }
            for t in payload["tiers"]
        ],
    }
    body.update(overrides)
    resp = await client.put(DEPOSIT_SETTINGS, json=body, headers=auth_header(admin_user))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: float):
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "test fund"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


async def _deposit(client: AsyncClient, user: dict, amount: float):
    resp = await client.post(
        f"{MY_DEPOSIT}/in", json={"amount": amount}, headers=auth_header(user)
    )
    assert resp.status_code == 201, resp.text


async def _make_order(
    client: AsyncClient, admin_user: dict, *, price: str = "100.00", compensation=None
) -> dict:
    body = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": price,
    }
    if compensation is not None:
        body["compensation_amount"] = compensation
    resp = await client.post("/orders/create", json=body, headers=auth_header(admin_user))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _accept(client: AsyncClient, user: dict, order_id: int):
    return await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(user)
    )


# ---------------------------------------------------------------------------
# 接单等待闸门
# ---------------------------------------------------------------------------


async def test_accept_has_no_wait_when_deposit_disabled(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """保证金模式关闭时，接单不受任何等待限制。"""
    order = await _make_order(client, admin_user)
    resp = await _accept(client, booster_user, order["id"])
    assert resp.status_code == 200, resp.text

    detail = await client.get(
        f"/orders/{order['id']}", headers=auth_header(booster_user)
    )
    assert detail.json()["accept_wait_seconds"] is None
    assert detail.json()["accept_available_at"] is None


async def test_accept_blocked_within_wait_window(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """开启后，无保证金打手（30 秒档）立即接单被拒并提示剩余秒数。"""
    await _enable_deposit(client, admin_user)
    order = await _make_order(client, admin_user)

    resp = await _accept(client, booster_user, order["id"])
    assert resp.status_code == 400, resp.text
    assert "才开放接单" in resp.json()["detail"]
    assert "30 秒" in resp.json()["detail"]


async def test_accept_window_exposed_to_frontend(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """订单详情把该用户档位的等待秒数与可接单时间下发给前端。"""
    await _enable_deposit(client, admin_user)
    order = await _make_order(client, admin_user)

    detail = (
        await client.get(f"/orders/{order['id']}", headers=auth_header(booster_user))
    ).json()
    assert detail["accept_wait_seconds"] == 30
    assert detail["accept_available_at"] is not None


async def test_top_tier_can_accept_immediately(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """保证金 1000 档等待 0 秒，可立即接单。"""
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, booster_user, 1500)
    await _deposit(client, booster_user, 1000)

    order = await _make_order(client, admin_user)
    resp = await _accept(client, booster_user, order["id"])
    assert resp.status_code == 200, resp.text

    detail = (
        await client.get(f"/orders/{order['id']}", headers=auth_header(booster_user))
    ).json()
    assert detail["accept_wait_seconds"] == 0


async def test_mid_tier_shortens_wait(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """保证金 300 档等待 20 秒。"""
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, booster_user, 400)
    await _deposit(client, booster_user, 300)

    order = await _make_order(client, admin_user)
    resp = await _accept(client, booster_user, order["id"])
    assert resp.status_code == 400
    assert "20 秒" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 免炸单赔付金
# ---------------------------------------------------------------------------


async def test_tier_exempts_compensation_freeze(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """保证金达到免除档位时，接单不再冻结炸单赔付金。"""
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, booster_user, 1500)
    await _deposit(client, booster_user, 1000)

    order = await _make_order(client, admin_user, compensation=20)
    assert Decimal(str(order["compensation_amount"])) == Decimal("20.00")

    resp = await _accept(client, booster_user, order["id"])
    assert resp.status_code == 200, resp.text

    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    # 保证金 1000：不冻结赔付金（冻结余额为 0）
    assert Decimal(str(wallet.frozen_balance)) == Decimal("0.00")
    assert Decimal(str(wallet.deposit_balance)) == Decimal("1000.00")


async def test_low_deposit_still_freezes_compensation(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """保证金不足 100 时仍按订单冻结 20 元赔付金。"""
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, booster_user, 500)
    await _deposit(client, booster_user, 50)

    # 交 50 属于最低档（等待 30 秒），先用顶档口径把等待绕开：直接调小等待
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
            },
            {
                "threshold": 100,
                "wait_seconds": 0,
                "exempt_compensation": True,
                "settle_hours": 72,
                "enabled": True,
            },
        ],
    )

    order = await _make_order(client, admin_user, compensation=20)
    resp = await _accept(client, booster_user, order["id"])
    assert resp.status_code == 200, resp.text

    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    assert Decimal(str(wallet.frozen_balance)) == Decimal("20.00")


async def test_order_gets_default_compensation_when_enabled(
    client: AsyncClient, admin_user: dict
):
    """保证金模式开启后，发单未指定赔付金时按默认 20 元兜底。"""
    await _enable_deposit(client, admin_user, default_compensation=20)
    order = await _make_order(client, admin_user)
    assert Decimal(str(order["compensation_amount"])) == Decimal("20.00")


async def test_order_has_no_default_compensation_when_disabled(
    client: AsyncClient, admin_user: dict
):
    """保证金模式关闭时不填充默认赔付金，保持原有行为。"""
    order = await _make_order(client, admin_user)
    assert order["compensation_amount"] is None


# ---------------------------------------------------------------------------
# 结账时效两种计时模式
# ---------------------------------------------------------------------------


async def _deliver_and_age(client: AsyncClient, booster_user: dict, order_id: int, *, hours_ago: float):
    """让打手交付，并把交付时间回拨到指定小时数之前。"""
    resp = await client.put(
        f"/orders/{order_id}/deliver", headers=auth_header(booster_user)
    )
    assert resp.status_code == 200, resp.text


async def test_settlement_mode_after_delivery_auto_settles(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """AFTER_DELIVERY：交付后经过档位时效即自动结算。"""
    await _enable_deposit(
        client,
        admin_user,
        settlement_mode="AFTER_DELIVERY",
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": False,
                "settle_hours": 1,
                "enabled": True,
            }
        ],
    )
    await _fund(client, admin_user, booster_user, 500)
    order = await _make_order(client, admin_user, price="100.00")
    assert (await _accept(client, booster_user, order["id"])).status_code == 200
    await _deliver_and_age(client, booster_user, order["id"], hours_ago=2)

    claim = (
        await db_session.execute(
            select(OrderClaim).where(OrderClaim.order_id == order["id"])
        )
    ).scalar_one()
    claim.delivered_at = datetime.now(timezone.utc) - timedelta(hours=2)
    claim.settlement_due_at = claim.delivered_at + timedelta(hours=1)
    await db_session.commit()

    settled = await scan_due_payouts(db_session)
    await db_session.commit()
    assert claim.id in settled

    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    # 测试充值 500 + 订单收入 100（赔付金 20 已随结算返还）
    assert Decimal(str(wallet.available_balance)) == Decimal("600.00")


async def test_after_approval_holds_until_due(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """AFTER_APPROVAL：审核通过不立即放款，到期后才自动结算。"""
    await _enable_deposit(
        client,
        admin_user,
        settlement_mode="AFTER_APPROVAL",
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": False,
                "settle_hours": 24,
                "enabled": True,
            }
        ],
    )
    await _fund(client, admin_user, booster_user, 500)
    order = await _make_order(client, admin_user, price="100.00")
    assert (await _accept(client, booster_user, order["id"])).status_code == 200
    await _deliver_and_age(client, booster_user, order["id"], hours_ago=0)

    claim = (
        await db_session.execute(
            select(OrderClaim).where(OrderClaim.order_id == order["id"])
        )
    ).scalar_one()

    # 老板审核通过：只记录时间，不立即入账
    resp = await client.put(
        f"/orders/{order['id']}/claims/{claim.id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    # 先结束本会话事务，才能看到 API 提交的 approved_at（REPEATABLE READ）
    await db_session.commit()
    await db_session.refresh(claim)
    assert claim.status == ClaimLifecycleStatus.DELIVERED
    assert claim.approved_at is not None

    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    # 500 充值；赔付金 20 已在交付时解冻（老板规则），押住的是单款到账时效
    assert Decimal(str(wallet.available_balance)) == Decimal("500.00")
    assert Decimal(str(wallet.frozen_balance)) == Decimal("0.00")

    # 未到期不结算
    assert await scan_due_payouts(db_session) == []
    await db_session.commit()

    # 把通过时间回拨到 25 小时前 → 到期应自动放款
    claim.approved_at = datetime.now(timezone.utc) - timedelta(hours=25)
    claim.settlement_due_at = claim.approved_at + timedelta(hours=24)
    await db_session.commit()

    settled = await scan_due_payouts(db_session)
    await db_session.commit()
    assert claim.id in settled

    await db_session.refresh(wallet)
    assert Decimal(str(wallet.available_balance)) == Decimal("600.00")


async def test_after_approval_waits_for_approval(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """AFTER_APPROVAL 且老板未审核时，不自动结算。"""
    await _enable_deposit(
        client,
        admin_user,
        settlement_mode="AFTER_APPROVAL",
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": False,
                "settle_hours": 1,
                "enabled": True,
            }
        ],
    )
    await _fund(client, admin_user, booster_user, 500)
    order = await _make_order(client, admin_user, price="100.00")
    assert (await _accept(client, booster_user, order["id"])).status_code == 200
    await _deliver_and_age(client, booster_user, order["id"], hours_ago=5)

    claim = (
        await db_session.execute(
            select(OrderClaim).where(OrderClaim.order_id == order["id"])
        )
    ).scalar_one()
    claim.delivered_at = datetime.now(timezone.utc) - timedelta(hours=5)
    await db_session.commit()

    # 未审核 → 不结算
    assert await scan_due_payouts(db_session) == []
    await db_session.commit()


async def test_after_approval_snapshots_review_terms_and_due_time(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """审核金额、扣款、备注和档位时效 remain fixed after approval."""
    await _enable_deposit(
        client,
        admin_user,
        settlement_mode="AFTER_APPROVAL",
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": False,
                "settle_hours": 24,
                "enabled": True,
            }
        ],
    )
    await _fund(client, admin_user, booster_user, 500)
    order = await _make_order(
        client, admin_user, price="100.00", compensation=20
    )
    assert (await _accept(client, booster_user, order["id"])).status_code == 200
    assert (
        await client.put(
            f"/orders/{order['id']}/deliver",
            headers=auth_header(booster_user),
        )
    ).status_code == 200

    claim = (
        await db_session.execute(
            select(OrderClaim).where(OrderClaim.order_id == order["id"])
        )
    ).scalar_one()
    # The timing snapshot is taken at delivery, before approval.
    assert claim.settlement_mode_snapshot == "AFTER_APPROVAL"
    assert claim.settle_hours_snapshot == 24
    await _enable_deposit(
        client,
        admin_user,
        settlement_mode="AFTER_APPROVAL",
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": False,
                "settle_hours": 1,
                "enabled": True,
            }
        ],
    )
    await db_session.commit()
    await db_session.refresh(claim)
    assert claim.settle_hours_snapshot == 24

    review = await client.put(
        f"/orders/{order['id']}/claims/{claim.id}/review",
        json={
            "action": "approve",
            "amount": "55.00",
            "deduction": "7.00",
            "note": "partial approval",
        },
        headers=auth_header(admin_user),
    )
    assert review.status_code == 200, review.text
    payload = review.json()
    assert payload["status"] == "DELIVERED"
    assert payload["approved_payout_amount"] == "55.00"
    assert payload["approved_deduction"] == "7.00"
    assert payload["approved_note"] == "partial approval"
    assert payload["settlement_mode_snapshot"] == "AFTER_APPROVAL"
    assert payload["settle_hours_snapshot"] == 24
    assert payload["settlement_due_at"] is not None

    await db_session.commit()
    await db_session.refresh(claim)
    approved_at = claim.approved_at
    due_at = claim.settlement_due_at
    assert approved_at is not None and due_at is not None
    assert due_at == approved_at + timedelta(hours=24)

    # Changing the active tier after review must not move this claim's due time.
    await _enable_deposit(
        client,
        admin_user,
        settlement_mode="AFTER_APPROVAL",
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": False,
                "settle_hours": 1,
                "enabled": True,
            }
        ],
    )
    await _deposit(client, booster_user, 100)
    await db_session.commit()
    await db_session.refresh(claim)
    assert claim.settlement_due_at == due_at

    duplicate = await client.put(
        f"/orders/{order['id']}/claims/{claim.id}/review",
        json={"action": "approve", "amount": "99.00"},
        headers=auth_header(admin_user),
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "该记录已审核通过，等待自动结算"

    assert await scan_due_payouts(
        db_session, now=approved_at + timedelta(hours=2)
    ) == []

    settled = await scan_due_payouts(
        db_session, now=due_at + timedelta(seconds=1)
    )
    await db_session.commit()
    assert claim.id in settled

    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    # 500 - 100 deposit transfer + 20 交付解冻 + 55 payout = 455；
    # 炸单扣除 7 改从保证金扣（100→93），解冻返还已在交付时完成。
    assert Decimal(str(wallet.available_balance)) == Decimal("455.00")
    assert Decimal(str(wallet.deposit_balance)) == Decimal("93.00")
    assert Decimal(str(wallet.frozen_balance)) == Decimal("0.00")



async def test_legacy_claim_falls_back_to_order_payout_delay(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """Claims without snapshots retain the pre-033 order delay behavior."""
    await _fund(client, admin_user, booster_user, 500)
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "payout_delay_hours": 1,
        },
        headers=auth_header(admin_user),
    )
    assert response.status_code == 201, response.text
    order = response.json()
    assert (await _accept(client, booster_user, order["id"])).status_code == 200
    assert (
        await client.put(
            f"/orders/{order['id']}/deliver",
            headers=auth_header(booster_user),
        )
    ).status_code == 200

    claim = (
        await db_session.execute(
            select(OrderClaim).where(OrderClaim.order_id == order["id"])
        )
    ).scalar_one()
    # Clear the post-033 fields to model a pre-033 legacy claim.
    claim.settlement_mode_snapshot = None
    claim.settle_hours_snapshot = None
    claim.settlement_due_at = None
    claim.delivered_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.commit()

    settled = await scan_due_payouts(db_session)
    await db_session.commit()
    assert claim.id in settled


# ---------------------------------------------------------------------------
# 炸单赔付：免的是接单预冻结，不是赔付责任
# ---------------------------------------------------------------------------


async def _accept_deliver_review(
    client: AsyncClient, admin_user: dict, booster_user: dict, *,
    compensation: float, deduction: float, settle_hours: int = 72,
):
    """走完 接单 → 交付 → 老板审核扣除 的全流程，返回 (order, claim)。"""
    await _enable_deposit(
        client,
        admin_user,
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": True,
                "settle_hours": settle_hours,
                "enabled": True,
            }
        ],
    )
    order = await _make_order(client, admin_user, compensation=compensation)
    assert (await _accept(client, booster_user, order["id"])).status_code == 200
    assert (
        await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))
    ).status_code == 200
    return order


async def test_exempt_booster_still_pays_compensation_from_deposit(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """有保证金（免预冻结）的打手炸单时，赔付从保证金里扣，不是免赔。"""
    await _enable_deposit(
        client,
        admin_user,
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 0,
                "exempt_compensation": True,
                "settle_hours": 72,
                "enabled": True,
            }
        ],
    )
    await _fund(client, admin_user, booster_user, 1000)
    await _deposit(client, booster_user, 500)

    order = await _make_order(client, admin_user, compensation=20)
    assert (await _accept(client, booster_user, order["id"])).status_code == 200

    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    # 免预冻结：接单时冻结余额保持 0
    assert Decimal(str(wallet.frozen_balance)) == Decimal("0.00")
    assert Decimal(str(wallet.deposit_balance)) == Decimal("500.00")

    await db_session.commit()
    assert (
        await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))
    ).status_code == 200

    claim_id = (
        await db_session.execute(
            select(OrderClaim.id).where(OrderClaim.order_id == order["id"])
        )
    ).scalar_one()

    resp = await client.put(
        f"/orders/{order['id']}/claims/{claim_id}/review",
        json={"action": "approve", "deduction": 20},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    await db_session.commit()
    await db_session.refresh(wallet)
    # 关键断言：真炸单要从保证金里扣 20，而不是免掉
    assert Decimal(str(wallet.deposit_balance)) == Decimal("480.00")
    assert Decimal(str(wallet.frozen_balance)) == Decimal("0.00")


async def test_delivery_releases_compensation_hold_immediately(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """老板规则：打手提交结单申请后，冻结的赔付金立即解冻回可用余额。"""
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
    await _fund(client, admin_user, booster_user, 500)

    order = await _make_order(client, admin_user, compensation=20)
    assert (await _accept(client, booster_user, order["id"])).status_code == 200

    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    assert Decimal(str(wallet.frozen_balance)) == Decimal("20.00")
    assert Decimal(str(wallet.available_balance)) == Decimal("480.00")

    await db_session.commit()
    assert (
        await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))
    ).status_code == 200

    await db_session.commit()
    await db_session.refresh(wallet)
    # 交付即解冻：20 立刻回到可用余额，不用等 72 小时结算
    assert Decimal(str(wallet.frozen_balance)) == Decimal("0.00")
    assert Decimal(str(wallet.available_balance)) == Decimal("500.00")
    assert Decimal(str(wallet.deposit_balance)) == Decimal("0.00")


async def test_deduction_after_delivery_draws_from_balance(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """交付即解冻后真炸单：赔付直接从打手余额里扣，不是扣不到。"""
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
    await _fund(client, admin_user, booster_user, 500)

    order = await _make_order(client, admin_user, compensation=20)
    assert (await _accept(client, booster_user, order["id"])).status_code == 200
    await db_session.commit()
    assert (
        await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))
    ).status_code == 200

    claim_id = (
        await db_session.execute(
            select(OrderClaim.id).where(OrderClaim.order_id == order["id"])
        )
    ).scalar_one()

    resp = await client.put(
        f"/orders/{order['id']}/claims/{claim_id}/review",
        json={"action": "approve", "deduction": 20},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    await db_session.commit()
    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    await db_session.refresh(wallet)
    # 解冻回 500 后炸单扣 20（直接落在可用余额），单款 100 同时入账
    assert Decimal(str(wallet.available_balance)) == Decimal("580.00")
    assert Decimal(str(wallet.frozen_balance)) == Decimal("0.00")
    assert Decimal(str(wallet.deposit_balance)) == Decimal("0.00")


async def test_deduction_prefers_deposit_over_available(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """扣除顺序：名额冻结 → 保证金 → 可用余额，保证金仍是第一担保。"""
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
            },
            {
                "threshold": 200,
                "wait_seconds": 0,
                "exempt_compensation": True,
                "settle_hours": 72,
                "enabled": True,
            },
        ],
    )
    await _fund(client, admin_user, booster_user, 550)
    # 保证金 50：仍落在 0 档（未豁免预冻结），但炸单时先扣保证金
    await _deposit(client, booster_user, 50)

    order = await _make_order(client, admin_user, compensation=20)
    assert (await _accept(client, booster_user, order["id"])).status_code == 200

    wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    assert Decimal(str(wallet.frozen_balance)) == Decimal("20.00")

    await db_session.commit()
    assert (
        await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))
    ).status_code == 200

    claim_id = (
        await db_session.execute(
            select(OrderClaim.id).where(OrderClaim.order_id == order["id"])
        )
    ).scalar_one()
    resp = await client.put(
        f"/orders/{order['id']}/claims/{claim_id}/review",
        json={"action": "approve", "deduction": 20},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text

    await db_session.commit()
    await db_session.refresh(wallet)
    # 扣除走保证金（50→30），交付解冻回来的可用余额只被单款入账 +100
    assert Decimal(str(wallet.deposit_balance)) == Decimal("30.00")
    assert Decimal(str(wallet.available_balance)) == Decimal("600.00")
    assert Decimal(str(wallet.frozen_balance)) == Decimal("0.00")
