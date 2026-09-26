"""全局服务费设置 + 发布时烙盘语义测试。

覆盖：
- 后台 GET/PUT /admin/service-fee/settings（含非管理员 403）；
- 管理员发单：开关开+不填费率 → 按全局烙盘；开关开+手填 → 手填优先；
  开关关 → 不收取；
- 烙盘保护：全局费率调整后，已发布订单的结算/回显不变；
- 编辑订单同理；非管理员编辑无法改服务费。
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.wallet_service import calculate_order_income
from tests.conftest import auth_header


def _order_payload(**overrides) -> dict:
    payload = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": "150.00",
        "description_raw": "全局服务费验证单",
    }
    payload.update(overrides)
    return payload


async def _set_global_rate(client: AsyncClient, admin_user: dict, rate: str) -> None:
    resp = await client.put(
        "/admin/service-fee/settings",
        json={"service_fee_rate": rate},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == f"{Decimal(rate):.2f}"


async def test_global_service_fee_settings_crud(
    client: AsyncClient, admin_user: dict
):
    """后台读取/保存全局服务费费率。"""
    resp = await client.get(
        "/admin/service-fee/settings", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "0.00"

    await _set_global_rate(client, admin_user, "8")

    resp = await client.get(
        "/admin/service-fee/settings", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "8.00"


async def test_global_service_fee_settings_non_admin_forbidden(
    client: AsyncClient, registered_user: dict
):
    """非管理员不能读写全局服务费设置。"""
    resp = await client.get(
        "/admin/service-fee/settings", headers=auth_header(registered_user)
    )
    assert resp.status_code == 403

    resp = await client.put(
        "/admin/service-fee/settings",
        json={"service_fee_rate": "50"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 403


async def test_global_service_fee_rate_out_of_range(
    client: AsyncClient, admin_user: dict
):
    """费率上限 100：越界 422。"""
    resp = await client.put(
        "/admin/service-fee/settings",
        json={"service_fee_rate": "101"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 422


async def test_publish_uses_global_rate_when_enabled_without_manual_rate(
    client: AsyncClient, admin_user: dict
):
    """开关开启 + 不填费率 → 按全局费率烙盘（150 × 8% → 138）。"""
    await _set_global_rate(client, admin_user, "8")

    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=True),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_rate"] == "8.00"
    assert order["service_fee_amount"] == "12.00"
    assert order["net_amount"] == "138.00"


async def test_publish_manual_rate_overrides_global(
    client: AsyncClient, admin_user: dict
):
    """开关开启 + 手填费率 → 逐单值优先于全局。"""
    await _set_global_rate(client, admin_user, "8")

    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=True, service_fee_rate="5"),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_rate"] == "5.00"
    assert order["service_fee_amount"] == "7.50"
    assert order["net_amount"] == "142.50"


async def test_publish_disabled_switch_never_charges(
    client: AsyncClient, admin_user: dict
):
    """开关关闭（默认）→ 不收服务费，即使全局费率已设置。"""
    await _set_global_rate(client, admin_user, "8")

    resp = await client.post(
        "/orders/create",
        json=_order_payload(),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_rate"] == "0.00"
    assert order["service_fee_amount"] == "0.00"
    assert order["net_amount"] == "150.00"


async def test_baked_order_unaffected_by_later_global_change(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """烙盘保护：全局费率改动不影响已发布订单的结算。"""
    await _set_global_rate(client, admin_user, "8")

    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=True),
        headers=auth_header(admin_user),
    )
    order_id = resp.json()["id"]

    # 全局改成 50%：已发订单的展示与结算都必须仍按 8%
    await _set_global_rate(client, admin_user, "50")

    resp = await client.get(f"/orders/{order_id}", headers=auth_header(admin_user))
    assert resp.json()["service_fee_rate"] == "8.00"
    assert resp.json()["net_amount"] == "138.00"

    # 结算链：接单 → 结单 → 审核，入账仍是 138.00
    await client.put(f"/orders/{order_id}/accept", headers=auth_header(booster_user))
    await client.put(f"/orders/{order_id}/deliver", headers=auth_header(booster_user))
    resp = await client.get(f"/orders/{order_id}/claims", headers=auth_header(admin_user))
    claim = resp.json()["items"][0]
    resp = await client.put(
        f"/orders/{order_id}/claims/{claim['id']}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200

    resp = await client.get("/wallet", headers=auth_header(booster_user))
    assert resp.json()["available_balance"] == "138.00"


async def test_legacy_null_rate_orders_settle_by_env_default(
    db_session: AsyncSession, admin_user: dict
):
    """存量 NULL 订单仍按 env COMMISSION_RATE（默认 0）全额结算。"""
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


async def test_edit_order_refreshes_to_current_global(
    client: AsyncClient, admin_user: dict
):
    """编辑时开开关不填费率 → 按当前全局重新烙盘；关闭 → 清除。"""
    await _set_global_rate(client, admin_user, "8")

    resp = await client.post(
        "/orders/create",
        json=_order_payload(),
        headers=auth_header(admin_user),
    )
    order_id = resp.json()["id"]

    # 全局改成 10%，编辑时开启服务费（不填费率）→ 烙 10%
    await _set_global_rate(client, admin_user, "10")
    resp = await client.put(
        f"/orders/{order_id}",
        json={"service_fee_enabled": True},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "10.00"
    assert resp.json()["net_amount"] == "135.00"

    # 关闭开关 → 不收取
    resp = await client.put(
        f"/orders/{order_id}",
        json={"service_fee_enabled": False},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "0.00"
    assert resp.json()["net_amount"] == "150.00"


async def test_edit_order_keeps_rate_when_fee_fields_omitted(
    client: AsyncClient, admin_user: dict
):
    """编辑订单不带服务费字段时，原费率保持不变。"""
    await _set_global_rate(client, admin_user, "8")

    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=True),
        headers=auth_header(admin_user),
    )
    order_id = resp.json()["id"]

    resp = await client.put(
        f"/orders/{order_id}",
        json={"notes": "改个备注"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "8.00"


async def test_non_admin_publish_ignores_service_fee_even_with_global(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session: AsyncSession,
):
    """普通用户发单即使全局已设费率也不收取（服务端强制清空）。"""
    from app.models.wallet import WalletTransactionType
    from app.services.wallet_service import WalletService

    await _set_global_rate(client, admin_user, "8")

    user_id = registered_user["user"]["id"]
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

    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=True, service_fee_rate="50"),
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_rate"] == "0.00"
    assert order["net_amount"] == "150.00"

    # 编辑自己的单也无法开启
    resp = await client.put(
        f"/orders/{order['id']}",
        json={"service_fee_enabled": True, "service_fee_rate": "50"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "0.00"
