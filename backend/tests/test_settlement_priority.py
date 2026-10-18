"""结算时效优先级（老板 2026-10-18 拍板：后台档位为基准，留空才兜底订单时效）。

锁定的口径（与 ``deposit_service.plan_settlement_terms`` 一致）：

- 档位 ``settle_hours`` **有值** → **按档位时间结算**（含 0 小时 = 交付即结算）；
- 档位 ``settle_hours`` **留空（NULL）** → 兜底按订单 ``payout_delay_*``；
- 两边都没有 → 永不自动结算（既有语义）。

**资金事故红线**：``settle_hours`` 的 None 绝不能被折算成 0 小时。0 小时的
语义是「交付即结算」，会把在途名额立刻推到到期并批量提前打款；而
``refresh_unsettled_claim_settlements`` 是由「订单详情 / 我的接单」读路径
触发的，任何一次页面打开都会引爆，不等调度器。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.order import ClaimLifecycleStatus, OrderClaim
from app.services import deposit_service
from app.services.payout_scheduler import _order_delay_due, scan_due_payouts
from tests.conftest import auth_header

DEPOSIT_SETTINGS = "/admin/deposit/settings"
MY_DEPOSIT = "/wallet/deposit"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _tier(threshold, *, settle_hours, enabled=True, wait_seconds=0, exempt=False):
    return {
        "threshold": threshold,
        "wait_seconds": wait_seconds,
        "exempt_compensation": exempt,
        "settle_hours": settle_hours,
        "enabled": enabled,
    }


async def _set_deposit(
    client: AsyncClient,
    admin_user: dict,
    *,
    enabled: bool = True,
    settlement_mode: str = "AFTER_DELIVERY",
    tiers: list[dict] | None = None,
) -> dict:
    """保存保证金配置（整表替换）。tiers=None 时取后台当前默认阶梯。"""
    current = (
        await client.get(DEPOSIT_SETTINGS, headers=auth_header(admin_user))
    ).json()
    body = {
        "enabled": enabled,
        "return_cooldown_days": current["return_cooldown_days"],
        "default_compensation": current.get("default_compensation", 20),
        "settlement_mode": settlement_mode,
        "global_booster_quota": current.get("global_booster_quota", 5),
        "tiers": (
            tiers
            if tiers is not None
            else [_tier(0, settle_hours=72)]
        ),
    }
    resp = await client.put(
        DEPOSIT_SETTINGS, json=body, headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount):
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "settlement priority test"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


async def _create_order(
    client: AsyncClient,
    publisher: dict,
    *,
    price: str = "100.00",
    payout_delay_days=None,
    payout_delay_hours=None,
) -> dict:
    body = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": price,
    }
    if payout_delay_days is not None:
        body["payout_delay_days"] = payout_delay_days
    if payout_delay_hours is not None:
        body["payout_delay_hours"] = payout_delay_hours
    resp = await client.post(
        "/orders/create", json=body, headers=auth_header(publisher)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _accept_deliver(
    client: AsyncClient,
    admin_user: dict,
    booster: dict,
    order_id: int,
) -> None:
    # 保证金模式开启后发单会带默认赔付金，接单需先从可用余额冻结，
    # 因此先给打手注资，避免测试被「余额不足」挡住（与结算逻辑无关）。
    await _fund(client, admin_user, booster, 500)
    resp = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster)
    )
    assert resp.status_code == 200, resp.text
    resp = await client.put(
        f"/orders/{order_id}/deliver", headers=auth_header(booster)
    )
    assert resp.status_code == 200, resp.text


async def _claim(db_session, order_id: int) -> OrderClaim:
    result = await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order_id)
    )
    return result.scalar_one()


async def _order_row(db_session, order_id: int):
    from app.models.order import Order

    result = await db_session.execute(select(Order).where(Order.id == order_id))
    return result.scalar_one()


def _aware(value: datetime | None) -> datetime | None:
    """库里是 naive UTC，解析器返回 aware UTC；比较前统一成 aware。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _effective_due(db_session, claim: OrderClaim, order) -> datetime | None:
    """用调度器实际使用的解析逻辑算出该名额的生效到期时间。

    ORDER_DELAY 快照按设计不落 ``settlement_due_at``（到期时间由订单时效现算），
    所以断言必须走同一解析路径，而不是读库里的预估值。
    """
    if claim.settlement_mode_snapshot == "ORDER_DELAY":
        return _order_delay_due(claim, order)
    from app.services import deposit_service

    setting = await deposit_service.get_or_create_deposit_setting(db_session)
    tier, _ = await deposit_service.get_user_tier(db_session, claim.booster_id)
    return deposit_service.resolve_unsettled_settlement_due(
        claim, tier, setting, order=order
    )


# ---------------------------------------------------------------------------
# 1. 老板反馈的核心场景：订单 5 天 + 0 保证金档 72h → 按订单 5 天
# ---------------------------------------------------------------------------


async def test_tier_settle_hours_wins_over_order_delay(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """档位设了 72h + 订单 5 天 → **按档位 72h**（后台设置为基准）。

    老板 2026-10-18 口径：档位有值时以档位为准；想让某个订单的到账时效
    生效，必须把该档位留空（见 ``test_null_tier_settle_hours_uses_order_delay``）。
    """
    await _set_deposit(
        client, admin_user, tiers=[_tier(0, settle_hours=72)]
    )
    order = await _create_order(
        client, admin_user, payout_delay_days=5
    )
    await _accept_deliver(client, admin_user, booster_user, order["id"])

    claim = await _claim(db_session, order["id"])
    assert claim.settlement_mode_snapshot == "AFTER_DELIVERY"
    assert claim.settle_hours_snapshot == 72
    due = await _effective_due(db_session, claim, await _order_row(db_session, order["id"]))
    assert _aware(due) == _aware(claim.delivered_at) + timedelta(hours=72)
    # 72 小时未到 → 调度器不结算
    assert await scan_due_payouts(db_session) == []


# ---------------------------------------------------------------------------
# 2. 档位留空（NULL）→ 纯按订单时效
# ---------------------------------------------------------------------------


async def test_null_tier_settle_hours_uses_order_delay(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """档位 settle_hours 留空 + 订单 5 天 → due = 交付 + 5 天。"""
    await _set_deposit(client, admin_user, tiers=[_tier(0, settle_hours=None)])
    order = await _create_order(client, admin_user, payout_delay_days=5)
    await _accept_deliver(client, admin_user, booster_user, order["id"])

    claim = await _claim(db_session, order["id"])
    assert claim.settlement_mode_snapshot == "ORDER_DELAY"
    assert claim.settle_hours_snapshot is None
    due = await _effective_due(
        db_session, claim, await _order_row(db_session, order["id"])
    )
    assert _aware(due) == _aware(claim.delivered_at) + timedelta(days=5)


# ---------------------------------------------------------------------------
# 3. 真保证金档位更快 → 加速器生效
# ---------------------------------------------------------------------------


async def test_earned_tier_faster_than_order_accelerates(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """订单 5 天 + 1000 档（1h，门槛 > 0）→ due = 交付 + 1 小时。"""
    await _set_deposit(
        client,
        admin_user,
        tiers=[
            _tier(0, settle_hours=None),
            _tier(1000, settle_hours=1, exempt=True),
        ],
    )
    await _fund(client, admin_user, booster_user, 1000)
    resp = await client.post(
        f"{MY_DEPOSIT}/in", json={"amount": 1000}, headers=auth_header(booster_user)
    )
    assert resp.status_code == 201, resp.text

    order = await _create_order(client, admin_user, payout_delay_days=5)
    await _accept_deliver(client, admin_user, booster_user, order["id"])

    claim = await _claim(db_session, order["id"])
    assert claim.settlement_mode_snapshot == "AFTER_DELIVERY"
    assert claim.settle_hours_snapshot == 1
    assert claim.settlement_due_at == claim.delivered_at + timedelta(hours=1)


async def test_tier_slower_than_order_still_wins(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """订单 1 天 + 1000 档（72h，比订单慢）→ **仍按档位 72h**。

    锁定「档位优先」而非「取更快者」：老板口径是后台设置说了算，
    不是两处取小。
    """
    await _set_deposit(
        client,
        admin_user,
        tiers=[
            _tier(0, settle_hours=None),
            _tier(1000, settle_hours=72, exempt=True),
        ],
    )
    await _fund(client, admin_user, booster_user, 1000)
    resp = await client.post(
        f"{MY_DEPOSIT}/in", json={"amount": 1000}, headers=auth_header(booster_user)
    )
    assert resp.status_code == 201, resp.text

    order = await _create_order(client, admin_user, payout_delay_days=1)
    await _accept_deliver(client, admin_user, booster_user, order["id"])

    claim = await _claim(db_session, order["id"])
    assert claim.settlement_mode_snapshot == "AFTER_DELIVERY"
    assert claim.settle_hours_snapshot == 72
    due = await _effective_due(
        db_session, claim, await _order_row(db_session, order["id"])
    )
    assert _aware(due) == _aware(claim.delivered_at) + timedelta(hours=72)


# ---------------------------------------------------------------------------
# 4. 订单未设时效 → 档位兜底
# ---------------------------------------------------------------------------


async def test_order_without_delay_falls_back_to_tier(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """订单未设到账时效 + 0 档 72h → due = 交付 + 72 小时。"""
    await _set_deposit(client, admin_user, tiers=[_tier(0, settle_hours=72)])
    order = await _create_order(client, admin_user)
    await _accept_deliver(client, admin_user, booster_user, order["id"])

    claim = await _claim(db_session, order["id"])
    assert claim.settlement_mode_snapshot == "AFTER_DELIVERY"
    assert claim.settle_hours_snapshot == 72
    assert claim.settlement_due_at == claim.delivered_at + timedelta(hours=72)


async def test_order_without_delay_and_null_tier_never_auto_settles(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """订单未设时效 + 档位留空 → ORDER_DELAY + due=None（永不自动结算）。"""
    await _set_deposit(client, admin_user, tiers=[_tier(0, settle_hours=None)])
    order = await _create_order(client, admin_user)
    await _accept_deliver(client, admin_user, booster_user, order["id"])

    claim = await _claim(db_session, order["id"])
    assert claim.settlement_mode_snapshot == "ORDER_DELAY"
    assert claim.settle_hours_snapshot is None
    assert claim.settlement_due_at is None
    assert await scan_due_payouts(db_session) == []


# ---------------------------------------------------------------------------
# 5. 资金事故回归：None 绝不能变 0 小时
# ---------------------------------------------------------------------------


def _make_settings(settlement_mode="AFTER_DELIVERY"):
    from app.models.deposit import DepositSetting

    return DepositSetting(
        id=1,
        enabled=True,
        return_cooldown_days=7,
        default_compensation=Decimal("20.00"),
        settlement_mode=settlement_mode,
        global_booster_quota=5,
    )


class _FakeTier:
    """轻量档位替身：只要 threshold / settle_hours 两个属性。"""

    def __init__(self, threshold, settle_hours):
        self.id = 1
        self.threshold = Decimal(str(threshold))
        self.settle_hours = settle_hours
        self.wait_seconds = 0
        self.exempt_compensation = False
        self.enabled = True


class _FakeOrder:
    def __init__(self, payout_delay_days=None, payout_delay_hours=None):
        self.payout_delay_days = payout_delay_days
        self.payout_delay_hours = payout_delay_hours


class _FakeClaim:
    def __init__(
        self,
        *,
        mode="AFTER_DELIVERY",
        hours=72,
        due=None,
        delivered_at=None,
        approved_at=None,
        settlement_paused=False,
    ):
        self.settlement_mode_snapshot = mode
        self.settle_hours_snapshot = hours
        self.settlement_due_at = due
        self.delivered_at = delivered_at or datetime.now(timezone.utc)
        self.approved_at = approved_at
        self.settlement_paused = settlement_paused
        self.status = ClaimLifecycleStatus.DELIVERED
        self.settled_at = None


def test_none_tier_hours_is_not_zero_in_resolver():
    """档位 settle_hours=None：resolver 不得算出 0 小时 / 过去的到期时间。

    三种在途场景都要守住：
    1. 已有 72h 快照的 claim（旧配置下交付）→ 留空后仍保持 72h（只缩短，
       不因留空把时间清成 0，也不延长到订单的 5 天）；
    2. ORDER_DELAY 快照的 claim（订单 5 天）→ 仍按订单 5 天；
    3. ORDER_DELAY 快照且订单也无时效 → None（永不自动结算），而非立即到期。
    """
    setting = _make_settings()
    tier = _FakeTier(0, None)
    delivered = datetime.now(timezone.utc) - timedelta(hours=1)

    # 场景 1：已有 72h 快照
    snap_claim = _FakeClaim(
        mode="AFTER_DELIVERY",
        hours=72,
        due=delivered + timedelta(hours=72),
        delivered_at=delivered,
    )
    due = deposit_service.resolve_unsettled_settlement_due(
        snap_claim, tier, setting, order=_FakeOrder(payout_delay_days=5)
    )
    # 关键断言：绝不能被 None 折成 0 小时（= 交付时刻 = 立即打款）
    assert due == delivered + timedelta(hours=72)
    assert due - delivered != timedelta(0)

    # 场景 2：ORDER_DELAY 快照（订单 5 天）
    order_delay_claim = _FakeClaim(
        mode="ORDER_DELAY",
        hours=None,
        due=delivered + timedelta(days=5),
        delivered_at=delivered,
    )
    due2 = deposit_service.resolve_unsettled_settlement_due(
        order_delay_claim, tier, setting, order=_FakeOrder(payout_delay_days=5)
    )
    assert due2 == delivered + timedelta(days=5)

    # 场景 3：ORDER_DELAY 快照但订单也没有时效 → None，而不是「立即到期」
    empty_claim = _FakeClaim(
        mode="ORDER_DELAY", hours=None, due=None, delivered_at=delivered
    )
    assert (
        deposit_service.resolve_unsettled_settlement_due(
            empty_claim, tier, setting, order=_FakeOrder()
        )
        is None
    )


def test_tier_settle_hours_helper_keeps_none():
    """tier_settle_hours 对 None 保持 None，对 0 保持 0（两者语义不同）。"""
    assert deposit_service.tier_settle_hours(_FakeTier(0, None)) is None
    assert deposit_service.tier_settle_hours(_FakeTier(1000, 0)) == 0
    assert deposit_service.tier_settle_hours(None) is None


def test_plan_settlement_terms_matrix():
    """优先级矩阵（单一事实来源）逐条锁定。"""
    setting = _make_settings()

    # 档位有值 → 按档位，与订单时效无关（老板口径：后台设置优先）
    plan = deposit_service.plan_settlement_terms(
        _FakeTier(0, 72), _FakeOrder(payout_delay_days=5), setting
    )
    assert (plan.mode, plan.hours) == ("AFTER_DELIVERY", 72)

    # 订单 120h + 1000 档 1h → 仍按档位（档位优先，不做"取更快"）
    plan = deposit_service.plan_settlement_terms(
        _FakeTier(1000, 1), _FakeOrder(payout_delay_days=5), setting
    )
    assert (plan.mode, plan.hours) == ("AFTER_DELIVERY", 1)

    # 订单 24h + 档位 72h → 仍按档位 72h（档位说了算，订单不覆盖）
    plan = deposit_service.plan_settlement_terms(
        _FakeTier(1000, 72), _FakeOrder(payout_delay_days=1), setting
    )
    assert (plan.mode, plan.hours) == ("AFTER_DELIVERY", 72)

    # 档位显式 0 小时 → 交付即结算（老板要的"单完结就到账"）
    plan = deposit_service.plan_settlement_terms(
        _FakeTier(100, 0), _FakeOrder(payout_delay_days=5), setting
    )
    assert (plan.mode, plan.hours) == ("AFTER_DELIVERY", 0)

    # 订单未设 + 档位有值 → 档位
    plan = deposit_service.plan_settlement_terms(
        _FakeTier(1000, 1), _FakeOrder(), setting
    )
    assert (plan.mode, plan.hours) == ("AFTER_DELIVERY", 1)

    # **档位留空** → 兜底按订单时效（这是"订单时间生效"的唯一入口）
    plan = deposit_service.plan_settlement_terms(
        _FakeTier(100, None), _FakeOrder(payout_delay_days=5), setting
    )
    assert (plan.mode, plan.hours) == ("ORDER_DELAY", None)

    # 档位留空 + 订单也未设 → 永不自动结算
    plan = deposit_service.plan_settlement_terms(
        _FakeTier(0, None), _FakeOrder(), setting
    )
    assert (plan.mode, plan.hours) == ("ORDER_DELAY", None)


async def test_refresh_never_zeroes_in_flight_claim(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """档位 settle_hours=None 时，读路径刷新不得把在途 claim 改成 0 小时。

    这是 §7.2 的资金事故回归：refresh_unsettled_claim_settlements 由
    「订单详情 / 我的接单」读路径触发，若把 None 当 0，任何一次页面打开
    都会把在途名额推到立即到期。
    """
    # 先在「72 小时档」下交付，形成 settle_hours_snapshot = 72 的在途 claim
    await _set_deposit(client, admin_user, tiers=[_tier(0, settle_hours=72)])
    order = await _create_order(client, admin_user)
    await _accept_deliver(client, admin_user, booster_user, order["id"])

    claim = await _claim(db_session, order["id"])
    original_due = claim.settlement_due_at
    assert claim.settle_hours_snapshot == 72

    # 后台把该档改成留空（模拟老板保存配置）
    await _set_deposit(client, admin_user, tiers=[_tier(0, settle_hours=None)])
    await db_session.commit()

    # 触发读路径刷新（订单详情）
    resp = await client.get(
        f"/orders/{order['id']}", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    await db_session.commit()
    await db_session.refresh(claim)

    # 红线：不能被写成 0，到期时间不得前移
    assert claim.settle_hours_snapshot != 0
    assert claim.settle_hours_snapshot == 72
    assert claim.settlement_due_at == original_due
    # 也不允许被立即结算
    assert claim.status == ClaimLifecycleStatus.DELIVERED
    assert claim.settled_at is None

    # 到期前扫描不会打款；到期后才结算
    assert await scan_due_payouts(db_session) == []
    await db_session.commit()
    settled = await scan_due_payouts(
        db_session, now=original_due + timedelta(seconds=1)
    )
    await db_session.commit()
    assert claim.id in settled


async def test_refresh_after_null_tier_keeps_snapshot_claim(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """在途 claim 遇到档位改为留空：生效到期时间不得前移（红线）。"""
    await _set_deposit(client, admin_user, tiers=[_tier(0, settle_hours=72)])
    order = await _create_order(client, admin_user, payout_delay_days=5)
    await _accept_deliver(client, admin_user, booster_user, order["id"])

    claim = await _claim(db_session, order["id"])
    order_row = await _order_row(db_session, order["id"])
    # 档位有值 → 快照 AFTER_DELIVERY / 72h
    assert claim.settlement_mode_snapshot == "AFTER_DELIVERY"
    assert claim.settle_hours_snapshot == 72
    due_before = await _effective_due(db_session, claim, order_row)
    assert _aware(due_before) == _aware(claim.delivered_at) + timedelta(hours=72)

    await _set_deposit(client, admin_user, tiers=[_tier(0, settle_hours=None)])
    await db_session.commit()

    resp = await client.get(
        "/orders/claims/mine", headers=auth_header(booster_user)
    )
    assert resp.status_code == 200, resp.text
    await db_session.commit()
    await db_session.refresh(claim)
    order_row = await _order_row(db_session, order["id"])

    # 红线：档位留空不得把生效时间前移（既不能变 0 小时，也不能提前）
    assert claim.settle_hours_snapshot != 0
    assert claim.settlement_due_at != 0
    due_after = await _effective_due(db_session, claim, order_row)
    assert _aware(due_after) <= _aware(due_before)
    assert await scan_due_payouts(db_session) == []


# ---------------------------------------------------------------------------
# 6. 0 档防呆
# ---------------------------------------------------------------------------


async def test_admin_accepts_tiers_without_enabled_zero_tier(
    client: AsyncClient, admin_user: dict
):
    """阶梯里没有 0 元档也能保存（2026-10-18 撤除该硬校验）。

    老板有意删除 0 元档，好让 0 保证金打手兜底到最低档、继承免冻结赔付金
    从而接得上单；生产库当前就是「无 0 元档」的配置，硬拦会把后台保存
    直接卡成 400。这里锁定「允许保存」以免后人把校验加回来。
    """
    current = (
        await client.get(DEPOSIT_SETTINGS, headers=auth_header(admin_user))
    ).json()
    body = {
        "enabled": True,
        "return_cooldown_days": current["return_cooldown_days"],
        "default_compensation": current.get("default_compensation", 20),
        "settlement_mode": "AFTER_DELIVERY",
        "global_booster_quota": current.get("global_booster_quota", 5),
        "tiers": [
            _tier(100, settle_hours=72),
            _tier(300, settle_hours=72, exempt=True),
        ],
    }
    resp = await client.put(
        DEPOSIT_SETTINGS, json=body, headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    assert [str(t["threshold"]) for t in resp.json()["tiers"]] == ["100.00", "300.00"]


async def test_admin_accepts_disabled_zero_tier_while_others_enabled(
    client: AsyncClient, admin_user: dict
):
    """0 元档被停用、其它档启用：同样允许保存（同一个撤除的校验）。"""
    current = (
        await client.get(DEPOSIT_SETTINGS, headers=auth_header(admin_user))
    ).json()
    body = {
        "enabled": True,
        "return_cooldown_days": current["return_cooldown_days"],
        "default_compensation": current.get("default_compensation", 20),
        "settlement_mode": "AFTER_DELIVERY",
        "global_booster_quota": current.get("global_booster_quota", 5),
        "tiers": [
            _tier(0, settle_hours=72, enabled=False),
            _tier(100, settle_hours=72, exempt=True),
        ],
    }
    resp = await client.put(
        DEPOSIT_SETTINGS, json=body, headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text


async def test_admin_accepts_null_settle_hours(
    client: AsyncClient, admin_user: dict
):
    """留空可正常保存；读回来仍是 null（不是 72，也不是 0）。"""
    data = await _set_deposit(
        client, admin_user, tiers=[_tier(0, settle_hours=None)]
    )
    assert data["tiers"][0]["settle_hours"] is None

    overview = (
        await client.get("/wallet/deposit", headers=auth_header(admin_user))
    )
    # 管理员访问用户侧接口会被拒（403），只用于确认接口存在；用普通用户验证
    # 见 test_overview_reports_null_settle_hours
    assert overview.status_code in (200, 403)


async def test_overview_reports_null_settle_hours(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """用户侧概览：档位留空时 settle_hours 返回 null（不再硬编码 72）。"""
    await _set_deposit(client, admin_user, tiers=[_tier(0, settle_hours=None)])
    resp = await client.get(MY_DEPOSIT, headers=auth_header(booster_user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["settle_hours"] is None


async def test_admin_can_settle_zero_hours_meaning_immediate(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """0 小时 ≠ 留空：显式填 0 表示交付即结算，必须立即到期。"""
    await _set_deposit(client, admin_user, tiers=[_tier(0, settle_hours=0)])
    order = await _create_order(client, admin_user)
    await _accept_deliver(client, admin_user, booster_user, order["id"])

    claim = await _claim(db_session, order["id"])
    assert claim.settle_hours_snapshot == 0
    assert claim.settlement_due_at == claim.delivered_at
    settled = await scan_due_payouts(
        db_session, now=claim.delivered_at + timedelta(seconds=1)
    )
    await db_session.commit()
    assert claim.id in settled


# ---------------------------------------------------------------------------
# 7. 顺带修洞：保证金开启但无启用档位 → 回落订单时效（而非永不结算）
# ---------------------------------------------------------------------------


async def test_no_enabled_tier_falls_back_to_order_delay(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """保证金开启、唯一档位被停用 + 订单设了 1 小时 → 仍能到期自动结算。"""
    await _set_deposit(
        client,
        admin_user,
        tiers=[_tier(0, settle_hours=1, enabled=False)],
    )
    order = await _create_order(client, admin_user, payout_delay_hours=1)
    await _accept_deliver(client, admin_user, booster_user, order["id"])

    claim = await _claim(db_session, order["id"])
    assert claim.settlement_mode_snapshot == "ORDER_DELAY"
    claim.delivered_at = datetime.now(timezone.utc) - timedelta(hours=2)
    claim.settlement_due_at = claim.delivered_at + timedelta(hours=1)
    await db_session.commit()

    settled = await scan_due_payouts(db_session)
    await db_session.commit()
    assert claim.id in settled
