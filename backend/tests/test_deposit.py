"""保证金测试：总开关、阶梯档位、余额划转与 7 天转回冷却期。

覆盖重点：
- 保证金模式默认关闭，未开启时不能缴纳，但仍可转回（不锁死资金）；
- 档位命中 = 门槛 ≤ 保证金余额 的最高一档；
- 转入/转回的余额校验与流水记录；
- 冷却期：有未完成订单 / 最后一单结算未满 7 天时禁止转回；
- 后台阶梯可整表替换，门槛不可重复，普通用户无权访问。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select

from app.models.deposit import DepositTier
from app.models.order import ClaimLifecycleStatus, OrderClaim, Order
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from app.services import deposit_service
from tests.conftest import auth_header

DEPOSIT_SETTINGS = "/admin/deposit/settings"
MY_DEPOSIT = "/wallet/deposit"


async def _enable_deposit(client: AsyncClient, admin_user: dict, **overrides):
    """打开保证金模式（默认阶梯）。"""
    resp = await client.get(DEPOSIT_SETTINGS, headers=auth_header(admin_user))
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    body = {
        "enabled": True,
        "return_cooldown_days": payload["return_cooldown_days"],
        "default_compensation": payload.get("default_compensation", 20),
        "settlement_mode": payload.get("settlement_mode", "AFTER_DELIVERY"),
        "global_booster_quota": payload.get("global_booster_quota", 5),
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
    resp = await client.put(
        DEPOSIT_SETTINGS, json=body, headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: float):
    """通过管理员调账给用户充值可用余额（便于测试缴纳保证金）。"""
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "test fund"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _make_order(admin_user: dict) -> Order:
    """构造一条满足非空约束的订单，用于挂接测试名额。"""
    return Order(
        user_id=admin_user["user"]["id"],
        game_name="王者荣耀",
        current_rank="钻石",
        target_rank="王者",
        title="测试订单",
        price=Decimal("10.00"),
    )


async def _transfer_in(client: AsyncClient, user: dict, amount: float):
    return await client.post(
        f"{MY_DEPOSIT}/in",
        json={"amount": amount},
        headers=auth_header(user),
    )


async def _transfer_out(client: AsyncClient, user: dict, amount: float):
    return await client.post(
        f"{MY_DEPOSIT}/out",
        json={"amount": amount},
        headers=auth_header(user),
    )


# ---------------------------------------------------------------------------
# 总开关
# ---------------------------------------------------------------------------


async def test_deposit_disabled_by_default(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    resp = await client.get(DEPOSIT_SETTINGS, headers=auth_header(admin_user))
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    overview = await client.get(MY_DEPOSIT, headers=auth_header(registered_user))
    assert overview.status_code == 200
    assert overview.json()["enabled"] is False

    denied = await _transfer_in(client, registered_user, 100)
    assert denied.status_code == 400
    assert "未开启" in denied.json()["detail"]


async def test_admin_can_enable_deposit_mode(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _enable_deposit(client, admin_user)
    overview = await client.get(MY_DEPOSIT, headers=auth_header(registered_user))
    assert overview.json()["enabled"] is True


async def test_deposit_settings_requires_admin(
    client: AsyncClient, registered_user: dict
):
    resp = await client.get(DEPOSIT_SETTINGS, headers=auth_header(registered_user))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 阶梯与档位
# ---------------------------------------------------------------------------


async def test_default_tiers_match_benefit_table(
    client: AsyncClient, admin_user: dict
):
    data = await _enable_deposit(client, admin_user)
    tiers = {str(t["threshold"]): t for t in data["tiers"]}
    assert tiers["0.00"]["wait_seconds"] == 30
    assert tiers["0.00"]["exempt_compensation"] is False
    assert tiers["100.00"]["exempt_compensation"] is True
    assert tiers["300.00"]["wait_seconds"] == 20
    assert tiers["500.00"]["wait_seconds"] == 10 and tiers["500.00"]["settle_hours"] == 48
    assert tiers["1000.00"]["wait_seconds"] == 0 and tiers["1000.00"]["settle_hours"] == 1


async def test_resolve_tier_by_balance(client: AsyncClient, db_session):
    tiers = await deposit_service.list_tiers(db_session)
    cases = [
        (Decimal("0"), Decimal("0.00")),
        (Decimal("99.99"), Decimal("0.00")),
        (Decimal("100"), Decimal("100.00")),
        (Decimal("299"), Decimal("100.00")),
        (Decimal("300"), Decimal("300.00")),
        (Decimal("550"), Decimal("500.00")),
        (Decimal("1000"), Decimal("1000.00")),
        (Decimal("99999"), Decimal("1000.00")),
    ]
    for balance, expected_threshold in cases:
        tier = deposit_service.resolve_tier(tiers, balance)
        assert Decimal(str(tier.threshold)) == expected_threshold, balance


async def test_overview_reports_tier_benefits(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, registered_user, 1000)

    resp = await _transfer_in(client, registered_user, 600)
    assert resp.status_code == 201, resp.text

    overview = (await client.get(MY_DEPOSIT, headers=auth_header(registered_user))).json()
    assert Decimal(str(overview["deposit_balance"])) == Decimal("600.00")
    assert Decimal(str(overview["current_threshold"])) == Decimal("500.00")
    assert overview["wait_seconds"] == 10
    assert overview["settle_hours"] == 48
    assert overview["exempt_compensation"] is True
    # 可用余额被相应扣减（1000 - 600）
    assert Decimal(str(overview["available_balance"])) == Decimal("400.00")


# ---------------------------------------------------------------------------
# 划转
# ---------------------------------------------------------------------------


async def test_transfer_in_moves_available_to_deposit(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, registered_user, 200)

    resp = await _transfer_in(client, registered_user, 150)
    assert resp.status_code == 201, resp.text

    user_id = registered_user["user"]["id"]
    wallet = (
        await db_session.execute(select(Wallet).where(Wallet.user_id == user_id))
    ).scalar_one()
    assert Decimal(str(wallet.deposit_balance)) == Decimal("150.00")
    assert Decimal(str(wallet.available_balance)) == Decimal("50.00")
    # 保证金不是收入，也不是冻结余额
    assert Decimal(str(wallet.frozen_balance)) == Decimal("0.00")
    assert Decimal(str(wallet.total_income)) == Decimal("0.00")

    tx = (
        await db_session.execute(
            select(WalletTransaction).where(
                WalletTransaction.type == WalletTransactionType.DEPOSIT_TRANSFER_IN
            )
        )
    ).scalar_one()
    assert Decimal(str(tx.amount)) == Decimal("-150.00")


async def test_transfer_in_rejects_insufficient_balance(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, registered_user, 50)
    resp = await _transfer_in(client, registered_user, 100)
    assert resp.status_code == 400
    assert "可用余额不足" in resp.json()["detail"]


async def test_transfer_out_rejects_more_than_deposit(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, registered_user, 200)
    await _transfer_in(client, registered_user, 100)

    resp = await _transfer_out(client, registered_user, 150)
    assert resp.status_code == 400
    assert "保证金余额不足" in resp.json()["detail"]


async def test_admin_cannot_use_deposit(
    client: AsyncClient, admin_user: dict
):
    await _enable_deposit(client, admin_user)
    resp = await client.get(MY_DEPOSIT, headers=auth_header(admin_user))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 7 天冷却期
# ---------------------------------------------------------------------------


async def test_transfer_out_allowed_without_orders(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    """从未结算过订单时没有冷却期，可直接转回。"""
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, registered_user, 200)
    await _transfer_in(client, registered_user, 100)

    resp = await _transfer_out(client, registered_user, 100)
    assert resp.status_code == 201, resp.text

    overview = (await client.get(MY_DEPOSIT, headers=auth_header(registered_user))).json()
    assert Decimal(str(overview["deposit_balance"])) == Decimal("0.00")


async def test_transfer_out_blocked_by_unfinished_orders(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    """还有未完成的名额时禁止转回。"""
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, registered_user, 200)
    await _transfer_in(client, registered_user, 100)

    user_id = registered_user["user"]["id"]
    order = _make_order(admin_user)
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderClaim(
            order_id=order.id,
            booster_id=user_id,
            status=ClaimLifecycleStatus.CLAIMED,
        )
    )
    await db_session.commit()

    resp = await _transfer_out(client, registered_user, 100)
    assert resp.status_code == 400
    assert "未完成的订单" in resp.json()["detail"]


async def test_transfer_out_blocked_within_cooldown(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    """最后一单结算未满 7 天时禁止转回，并给出剩余时间。"""
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, registered_user, 200)
    await _transfer_in(client, registered_user, 100)

    user_id = registered_user["user"]["id"]
    order = _make_order(admin_user)
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderClaim(
            order_id=order.id,
            booster_id=user_id,
            status=ClaimLifecycleStatus.SETTLED,
            settled_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    await db_session.commit()

    resp = await _transfer_out(client, registered_user, 100)
    assert resp.status_code == 400
    assert "需满 7 天" in resp.json()["detail"]


async def test_transfer_out_allowed_after_cooldown(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    """最后一单结算已满 7 天即可转回。"""
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, registered_user, 200)
    await _transfer_in(client, registered_user, 100)

    user_id = registered_user["user"]["id"]
    order = _make_order(admin_user)
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderClaim(
            order_id=order.id,
            booster_id=user_id,
            status=ClaimLifecycleStatus.SETTLED,
            settled_at=datetime.now(timezone.utc) - timedelta(days=8),
        )
    )
    await db_session.commit()

    resp = await _transfer_out(client, registered_user, 100)
    assert resp.status_code == 201, resp.text


async def test_transfer_out_still_allowed_when_mode_disabled(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    """总开关关闭后仍允许转回，避免资金被锁死。"""
    await _enable_deposit(client, admin_user)
    await _fund(client, admin_user, registered_user, 200)
    await _transfer_in(client, registered_user, 100)

    await _enable_deposit(client, admin_user, enabled=False)

    resp = await _transfer_out(client, registered_user, 100)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# 后台阶梯维护
# ---------------------------------------------------------------------------


async def test_admin_can_replace_tiers(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _enable_deposit(
        client,
        admin_user,
        tiers=[
            {
                "threshold": 0,
                "wait_seconds": 60,
                "exempt_compensation": False,
                "settle_hours": 96,
                "enabled": True,
            },
            {
                "threshold": 2000,
                "wait_seconds": 0,
                "exempt_compensation": True,
                "settle_hours": 0,
                "enabled": True,
            },
        ],
    )
    overview = (await client.get(MY_DEPOSIT, headers=auth_header(registered_user))).json()
    assert [str(t["threshold"]) for t in overview["tiers"]] == ["0.00", "2000.00"]
    assert overview["wait_seconds"] == 60
    assert overview["settle_hours"] == 96


async def test_admin_tiers_reject_duplicate_threshold(
    client: AsyncClient, admin_user: dict
):
    resp = await client.put(
        DEPOSIT_SETTINGS,
        json={
            "enabled": True,
            "return_cooldown_days": 7,
            "tiers": [
                {"threshold": 100, "wait_seconds": 10, "exempt_compensation": True, "settle_hours": 24, "enabled": True},
                {"threshold": 100, "wait_seconds": 0, "exempt_compensation": True, "settle_hours": 1, "enabled": True},
            ],
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400
    assert "不能重复" in resp.json()["detail"]


async def test_admin_can_change_cooldown_days(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    await _enable_deposit(client, admin_user, return_cooldown_days=1)
    await _fund(client, admin_user, registered_user, 200)
    await _transfer_in(client, registered_user, 100)

    user_id = registered_user["user"]["id"]
    order = _make_order(admin_user)
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderClaim(
            order_id=order.id,
            booster_id=user_id,
            status=ClaimLifecycleStatus.SETTLED,
            settled_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
    )
    await db_session.commit()

    resp = await _transfer_out(client, registered_user, 100)
    assert resp.status_code == 201, resp.text


async def test_tiers_table_seeded_with_defaults(
    client: AsyncClient, db_session
):
    """阶梯表为空时自动补齐默认阶梯（新建库场景）。"""
    tiers = await deposit_service.list_tiers(db_session)
    assert len(tiers) == 5
    stored = (await db_session.execute(select(DepositTier))).scalars().all()
    assert len(stored) == 5
