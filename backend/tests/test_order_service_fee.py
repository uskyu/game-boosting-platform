"""Per-order service fee tests.

覆盖逐单服务费（百分比）的资金语义：
- 管理员发布/编辑可设置；非管理员设置被服务端强制忽略；
- 打手结算入账 = price × (1 - 有效费率)，与 OrderResponse 的
  service_fee_amount / net_amount 同舍入口径；
- 未设置费率回退平台全局 COMMISSION_RATE，旧订单行为不变；
- 审核时显式到账金额仍然优先（可按全额打款豁免服务费）。
"""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.money import resolve_service_fee_rate
from app.models.wallet import Wallet, WalletTransactionType
from app.services.wallet_service import WalletService, calculate_order_income
from tests.conftest import auth_header


def _order_payload(**overrides) -> dict:
    payload = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": "150.00",
        "description_raw": "钻石上王者",
    }
    payload.update(overrides)
    return payload


async def test_admin_create_order_with_service_fee(
    client: AsyncClient, admin_user: dict
):
    """管理员设 8%：三段拆分与截图一致（150 / -12.00 / 138.00）。"""
    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_rate="8"),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_rate"] == "8.00"
    assert order["service_fee_amount"] == "12.00"
    assert order["net_amount"] == "138.00"


async def test_create_order_without_fee_is_unchanged(
    client: AsyncClient, admin_user: dict
):
    """不设费率：服务费 0，实际到账 = 订单金额（与旧行为一致）。"""
    resp = await client.post(
        "/orders/create",
        json=_order_payload(),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_rate"] == "0.00"
    assert order["service_fee_amount"] == "0.00"
    assert order["net_amount"] == order["price"] == "150.00"


async def test_service_fee_rate_bounds(client: AsyncClient, admin_user: dict):
    """费率上限 100：超出按 schema 422 拒绝。"""
    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_rate="101"),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 422


async def test_resolve_service_fee_rate_units_and_clamp(monkeypatch):
    """单位换算：百分数进、小数出；None 回退全局；越界收敛。"""
    assert resolve_service_fee_rate(None) == Decimal("0")
    assert resolve_service_fee_rate(Decimal("8")) == Decimal("0.08")
    assert resolve_service_fee_rate("7.5") == Decimal("0.075")
    assert resolve_service_fee_rate(-1) == Decimal("0")
    assert resolve_service_fee_rate(1000) == Decimal("1")

    monkeypatch.setattr(settings, "COMMISSION_RATE", 0.05)
    assert resolve_service_fee_rate(None) == Decimal("0.05")
    assert resolve_service_fee_rate(Decimal("8")) == Decimal("0.08")


async def test_calculate_order_income_matches_response(
    client: AsyncClient, admin_user: dict, monkeypatch
):
    """结算函数与响应展示同口径；全局费率对未设费率订单生效。"""
    resp = await client.post(
        "/orders/create",
        json=_order_payload(price="99.99"),
        headers=auth_header(admin_user),
    )
    order = resp.json()
    # 未设逐单费率且平台默认为 0 → 全额
    assert order["net_amount"] == "99.99"
    assert order["service_fee_amount"] == "0.00"

    # 直接以轻量对象验证逐单费率与全局回退两条路径
    class _Order:
        price = Decimal("99.99")
        service_fee_rate = None

    o = _Order()
    assert calculate_order_income(o) == Decimal("99.99")
    o.service_fee_rate = Decimal("8")
    assert calculate_order_income(o) == Decimal("91.99")  # 99.99 × 0.92 = 91.9908

    monkeypatch.setattr(settings, "COMMISSION_RATE", 0.05)
    o.service_fee_rate = None
    assert calculate_order_income(o) == Decimal("94.99")  # 99.99 × 0.95 = 94.9905


async def test_service_fee_settlement_lifecycle(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
):
    """完整链路：设 8% → 接单 → 结单 → 审核通过，打手入账 = 净额 138.00。"""
    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_rate="8"),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order_id = resp.json()["id"]

    resp = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster_user)
    )
    assert resp.status_code == 200

    resp = await client.put(
        f"/orders/{order_id}/deliver", headers=auth_header(booster_user)
    )
    assert resp.status_code == 200

    resp = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(admin_user)
    )
    claim = resp.json()["items"][0]

    # 审核通过（不传 amount）：默认按净额结算（立即结算路径直接入账）
    resp = await client.put(
        f"/orders/{order_id}/claims/{claim['id']}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    approved = resp.json()
    assert approved["status"] == "SETTLED"

    # 打手钱包入账 = 净额 138.00（150 - 12 服务费）
    resp = await client.get("/wallet", headers=auth_header(booster_user))
    assert resp.json()["available_balance"] == "138.00"

    # 订单详情回显三段拆分
    resp = await client.get(f"/orders/{order_id}", headers=auth_header(admin_user))
    data = resp.json()
    assert data["service_fee_rate"] == "8.00"
    assert data["service_fee_amount"] == "12.00"
    assert data["net_amount"] == "138.00"


async def test_review_explicit_amount_overrides_fee(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """审核显式到账金额优先于服务费（老板可按全额打款）。"""
    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_rate="8"),
        headers=auth_header(admin_user),
    )
    order_id = resp.json()["id"]

    await client.put(f"/orders/{order_id}/accept", headers=auth_header(booster_user))
    await client.put(f"/orders/{order_id}/deliver", headers=auth_header(booster_user))
    resp = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(admin_user)
    )
    claim = resp.json()["items"][0]

    # 显式按全额 150 审核：服务费被豁免，打手实收 150.00
    resp = await client.put(
        f"/orders/{order_id}/claims/{claim['id']}/review",
        json={"action": "approve", "amount": "150.00"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "SETTLED"

    resp = await client.get("/wallet", headers=auth_header(booster_user))
    assert resp.json()["available_balance"] == "150.00"


async def test_non_admin_cannot_set_or_edit_service_fee(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session: AsyncSession,
):
    """普通用户发单传费率被强制清空；编辑自己的单也无法改费率。"""
    user_id = registered_user["user"]["id"]

    # 给普通用户充值可用余额（发单需托管 price × max_claims）
    wallet_service = WalletService(db_session)
    wallet = await wallet_service.get_or_create_wallet(user_id)
    await wallet_service.credit(
        wallet,
        amount=Decimal("1000.00"),
        tx_type=WalletTransactionType.ADMIN_ADJUST,
        operator_id=1,
        remark="test top-up",
    )
    await db_session.commit()

    # 发单时传 50%：落库必须为 None（响应按 0% 展示）
    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_rate="50"),
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_rate"] == "0.00"
    assert order["net_amount"] == order["price"] == "150.00"
    order_id = order["id"]

    # 编辑自己的待接单订单时传 50%：值保持不变
    resp = await client.put(
        f"/orders/{order_id}",
        json={"service_fee_rate": "50"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "0.00"

    # 管理员可以改
    resp = await client.put(
        f"/orders/{order_id}",
        json={"service_fee_rate": "10"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "10.00"
    assert resp.json()["service_fee_amount"] == "15.00"
    assert resp.json()["net_amount"] == "135.00"


async def test_admin_can_edit_service_fee(
    client: AsyncClient, admin_user: dict
):
    """管理员编辑费率：从 8% 改为 0 后展示随之更新。"""
    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_rate="8"),
        headers=auth_header(admin_user),
    )
    order_id = resp.json()["id"]

    resp = await client.put(
        f"/orders/{order_id}",
        json={"service_fee_rate": "0"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_amount"] == "0.00"
    assert resp.json()["net_amount"] == "150.00"


async def test_old_orders_without_column_value(
    db_session: AsyncSession, admin_user: dict
):
    """存量订单（service_fee_rate 为 NULL）结算不受影响。"""
    from app.models.order import Order

    order = Order(
        user_id=admin_user["user"]["id"],
        game_name="王者荣耀",
        current_rank="钻石",
        target_rank="王者",
        price=Decimal("150.00"),
        price_min=Decimal("150.00"),
        price_max=Decimal("150.00"),
        status="PENDING",
        priority=0,
    )
    db_session.add(order)
    await db_session.commit()

    assert order.service_fee_rate is None
    assert calculate_order_income(order) == Decimal("150.00")


@pytest.mark.parametrize(
    "price,rate,expected_fee,expected_net",
    [
        ("150.00", "8", "12.00", "138.00"),
        ("99.99", "8", "8.00", "91.99"),
        ("0.05", "50", "0.03", "0.02"),
        ("150.00", "0", "0.00", "150.00"),
        ("150.00", "100", "150.00", "0.00"),
    ],
)
async def test_fee_rounding_matrix(
    client: AsyncClient,
    admin_user: dict,
    price,
    rate,
    expected_fee,
    expected_net,
):
    """分位 ROUND_HALF_UP 舍入矩阵（与结算同一函数口径）。"""
    resp = await client.post(
        "/orders/create",
        json=_order_payload(price=price, service_fee_rate=rate),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_amount"] == expected_fee
    assert order["net_amount"] == expected_net
