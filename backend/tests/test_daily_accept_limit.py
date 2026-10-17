"""每日接单量权益测试：档位上限、12 点窗口口径、取消不计数、管理端派单不受限。

覆盖重点：
- 未配置上限（NULL）时接单不受任何限制，保持原有行为；
- 达到当前档位上限后接单 400，detail 带四项权益信息（code/benefit/level/current/limit）；
- 统计口径：北京时间每日 12:00 刷新的窗口内、订单未取消才计数；
- 管理端派单路径不施加该闸门（与等待闸门口径一致）；
- 「查看权益」接口暴露当前档位上限、今日已接数与下次刷新时间。
"""

from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import select

from app.models.order import OrderClaim
from app.services.deposit_service import daily_accept_window
from tests.conftest import auth_header

DEPOSIT_SETTINGS = "/admin/deposit/settings"
MY_DEPOSIT = "/wallet/deposit"


async def _set_tiers(
    client: AsyncClient,
    admin_user: dict,
    *,
    tiers: list[dict],
) -> None:
    """开启保证金模式并整表替换阶梯（等待秒数统一 0，隔离等待闸门）。"""
    payload = (await client.get(DEPOSIT_SETTINGS, headers=auth_header(admin_user))).json()
    body = {
        "enabled": True,
        "return_cooldown_days": payload["return_cooldown_days"],
        "default_compensation": payload.get("default_compensation", 20),
        "settlement_mode": payload.get("settlement_mode", "AFTER_DELIVERY"),
        "tiers": tiers,
    }
    resp = await client.put(DEPOSIT_SETTINGS, json=body, headers=auth_header(admin_user))
    assert resp.status_code == 200, resp.text


async def _make_order(client: AsyncClient, admin_user: dict, *, price: str = "100.00") -> dict:
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
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _accept(client: AsyncClient, user: dict, order_id: int):
    return await client.put(f"/orders/{order_id}/accept", headers=auth_header(user))


def _tier(threshold: int, *, daily_accept_limit: int | None) -> dict:
    return {
        "threshold": threshold,
        "wait_seconds": 0,
        "exempt_compensation": True,
        "settle_hours": 72,
        "daily_accept_limit": daily_accept_limit,
        "enabled": True,
    }


# ---------------------------------------------------------------------------
# 上限闸门
# ---------------------------------------------------------------------------


async def test_unlimited_tier_never_blocks(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """未配置上限（NULL）时接单不受限制。"""
    await _set_tiers(client, admin_user, tiers=[_tier(0, daily_accept_limit=None)])
    for _ in range(3):
        order = await _make_order(client, admin_user)
        assert (await _accept(client, booster_user, order["id"])).status_code == 200


async def test_zero_limit_treated_as_unlimited(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """0 与 NULL 同义 = 不限。"""
    await _set_tiers(client, admin_user, tiers=[_tier(0, daily_accept_limit=0)])
    order = await _make_order(client, admin_user)
    assert (await _accept(client, booster_user, order["id"])).status_code == 200


async def test_daily_limit_blocks_with_benefit_payload(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """达到上限后接单 400，detail 带四项权益信息。"""
    await _set_tiers(client, admin_user, tiers=[_tier(0, daily_accept_limit=1)])

    first = await _make_order(client, admin_user)
    assert (await _accept(client, booster_user, first["id"])).status_code == 200

    second = await _make_order(client, admin_user)
    resp = await _accept(client, booster_user, second["id"])
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "daily_accept_limit"
    assert detail["current"] == 1
    assert detail["limit"] == 1
    assert detail["level"] == 1
    assert detail["benefit"] == "默认档"
    assert "已达上限" in detail["message"]


async def test_daily_limit_level_reflects_tier_order(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """多档位时等级 = 生效档按门槛升序的 1-based 序号，标签带门槛值。"""
    await _set_tiers(
        client,
        admin_user,
        tiers=[
            _tier(0, daily_accept_limit=5),
            _tier(1000, daily_accept_limit=1),
        ],
    )
    # 打手保证金 1000 → 命中第 2 档
    fund = await client.post(
        f"/admin/wallets/{booster_user['user']['id']}/adjust",
        json={"amount": 1500, "reason": "test fund"},
        headers=auth_header(admin_user),
    )
    assert fund.status_code in (200, 201), fund.text
    transfer = await client.post(
        f"{MY_DEPOSIT}/in", json={"amount": 1000}, headers=auth_header(booster_user)
    )
    assert transfer.status_code == 201, transfer.text

    first = await _make_order(client, admin_user)
    assert (await _accept(client, booster_user, first["id"])).status_code == 200

    second = await _make_order(client, admin_user)
    resp = await _accept(client, booster_user, second["id"])
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["level"] == 2
    assert detail["benefit"] == "保证金 ¥1000 档"


# ---------------------------------------------------------------------------
# 统计口径：12 点窗口 + 取消不计数
# ---------------------------------------------------------------------------


async def test_cancelled_claim_not_counted(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """被取消的接单不计入每日接单量。"""
    await _set_tiers(client, admin_user, tiers=[_tier(0, daily_accept_limit=1)])

    first = await _make_order(client, admin_user)
    assert (await _accept(client, booster_user, first["id"])).status_code == 200

    claim = (
        await db_session.execute(
            select(OrderClaim).where(OrderClaim.order_id == first["id"])
        )
    ).scalar_one()
    claim.status = "CANCELLED"
    await db_session.commit()

    second = await _make_order(client, admin_user)
    assert (await _accept(client, booster_user, second["id"])).status_code == 200


async def test_claim_before_window_start_not_counted(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """窗口（北京时间今日/昨日 12:00）之前的接单不计入。"""
    await _set_tiers(client, admin_user, tiers=[_tier(0, daily_accept_limit=1)])

    first = await _make_order(client, admin_user)
    assert (await _accept(client, booster_user, first["id"])).status_code == 200

    window_start, _ = daily_accept_window(datetime.now(timezone.utc))
    claim = (
        await db_session.execute(
            select(OrderClaim).where(OrderClaim.order_id == first["id"])
        )
    ).scalar_one()
    claim.created_at = window_start - timedelta(hours=1)
    await db_session.commit()

    second = await _make_order(client, admin_user)
    assert (await _accept(client, booster_user, second["id"])).status_code == 200


async def test_claim_inside_window_counted(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """窗口内的接单计数（刚发生的接单 created_at 必在窗口内）。"""
    await _set_tiers(client, admin_user, tiers=[_tier(0, daily_accept_limit=1)])

    first = await _make_order(client, admin_user)
    assert (await _accept(client, booster_user, first["id"])).status_code == 200

    window_start, next_refresh = daily_accept_window(datetime.now(timezone.utc))
    claim = (
        await db_session.execute(
            select(OrderClaim).where(OrderClaim.order_id == first["id"])
        )
    ).scalar_one()
    assert claim.created_at >= window_start
    assert next_refresh == window_start + timedelta(days=1)


# ---------------------------------------------------------------------------
# 管理端派单不受限
# ---------------------------------------------------------------------------


async def test_admin_assign_bypasses_daily_limit(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """管理端派单不施加每日接单量闸门（与等待闸门口径一致）。"""
    await _set_tiers(client, admin_user, tiers=[_tier(0, daily_accept_limit=1)])

    first = await _make_order(client, admin_user)
    assert (await _accept(client, booster_user, first["id"])).status_code == 200

    second = await _make_order(client, admin_user)
    resp = await client.put(
        f"/admin/orders/{second['id']}/assign",
        json={"booster_id": booster_user["user"]["id"], "reason": "test assign"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 查看权益接口字段
# ---------------------------------------------------------------------------


async def test_deposit_overview_exposes_daily_fields(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """「查看权益」暴露当前档位上限、今日已接数与下次刷新时间。"""
    await _set_tiers(client, admin_user, tiers=[_tier(0, daily_accept_limit=2)])

    order = await _make_order(client, admin_user)
    assert (await _accept(client, booster_user, order["id"])).status_code == 200

    resp = await client.get(MY_DEPOSIT, headers=auth_header(booster_user))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["daily_accept_limit"] == 2
    assert data["daily_accepted_count"] == 1
    assert data["daily_limit_reset_at"] is not None
    tier = next(t for t in data["tiers"] if float(t["threshold"]) == 0)
    assert tier["daily_accept_limit"] == 2


async def test_admin_tier_upsert_roundtrip(
    client: AsyncClient, admin_user: dict
):
    """管理端保存后 daily_accept_limit 原样回读；0 归一为 null。"""
    await _set_tiers(
        client,
        admin_user,
        tiers=[_tier(0, daily_accept_limit=3), _tier(500, daily_accept_limit=0)],
    )
    payload = (await client.get(DEPOSIT_SETTINGS, headers=auth_header(admin_user))).json()
    by_threshold = {float(t["threshold"]): t for t in payload["tiers"]}
    assert by_threshold[0]["daily_accept_limit"] == 3
    assert by_threshold[500]["daily_accept_limit"] is None
