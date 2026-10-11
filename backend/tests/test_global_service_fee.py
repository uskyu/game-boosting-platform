"""全局服务费设置 + 发布时烙盘语义测试。

覆盖：
- 后台 GET/PUT /admin/service-fee/settings（含非管理员 403）；
- 管理员发单：开关开+不填费率 → 按全局烙盘；开关开+手填 → 手填优先；
  开关关 → 不收取；
- 烙盘保护：全局费率调整后，已发布订单的结算/回显不变；
- 编辑订单同理；非管理员与管理员同口径（2026-10-11 修复：此前非管理员
  发布者一律不收服务费，全局设置只对老板自己的账号生效）。
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


async def _set_individual_fee_enabled(
    client: AsyncClient, admin_user: dict, enabled: bool
) -> None:
    resp = await client.put(
        "/admin/service-fee/settings",
        json={"individual_service_fee_enabled": enabled},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["individual_service_fee_enabled"] is enabled


async def test_global_service_fee_settings_crud(
    client: AsyncClient, admin_user: dict
):
    """后台读取/保存全局服务费费率。"""
    resp = await client.get(
        "/admin/service-fee/settings", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "0.00"
    assert resp.json()["individual_service_fee_enabled"] is False

    await _set_global_rate(client, admin_user, "8")
    await _set_individual_fee_enabled(client, admin_user, True)

    resp = await client.get(
        "/admin/service-fee/settings", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "8.00"
    assert resp.json()["individual_service_fee_enabled"] is True

    # Updating the rate alone must preserve the existing toggle.
    await _set_global_rate(client, admin_user, "9")
    resp = await client.get(
        "/admin/service-fee/settings", headers=auth_header(admin_user)
    )
    assert resp.json()["service_fee_rate"] == "9.00"
    assert resp.json()["individual_service_fee_enabled"] is True


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
    resp = await client.put(
        "/admin/service-fee/settings",
        json={"individual_service_fee_enabled": True},
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
    await _set_individual_fee_enabled(client, admin_user, True)

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
    """逐单设置开启后，手填费率优先于全局。"""
    await _set_global_rate(client, admin_user, "8")
    await _set_individual_fee_enabled(client, admin_user, True)

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


async def test_individual_fee_enabled_can_turn_fee_off_for_one_order(
    client: AsyncClient, admin_user: dict
):
    """逐单设置开启时，仍可对单个订单关闭服务费。"""
    await _set_global_rate(client, admin_user, "8")
    await _set_individual_fee_enabled(client, admin_user, True)

    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=False),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_rate"] == "0.00"
    assert order["service_fee_amount"] == "0.00"
    assert order["net_amount"] == "150.00"


async def test_individual_fee_disabled_uses_global_and_ignores_overrides(
    client: AsyncClient, admin_user: dict
):
    """逐单设置总开关关闭：新单按全局费率烙盘，客户端逐单字段被忽略。"""
    await _set_global_rate(client, admin_user, "8")

    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=True, service_fee_rate="5"),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_rate"] == "8.00"
    assert order["service_fee_amount"] == "12.00"
    assert order["net_amount"] == "138.00"


async def test_individual_fee_disabled_bakes_current_global(
    client: AsyncClient, admin_user: dict
):
    """总开关关闭时同样烙盘：全局改动只影响之后发布的订单。"""
    await _set_global_rate(client, admin_user, "8")

    resp = await client.post(
        "/orders/create",
        json=_order_payload(),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    first_id = resp.json()["id"]
    assert resp.json()["service_fee_rate"] == "8.00"

    await _set_global_rate(client, admin_user, "50")
    resp = await client.post(
        "/orders/create",
        json=_order_payload(),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    assert resp.json()["service_fee_rate"] == "50.00"

    resp = await client.get(f"/orders/{first_id}", headers=auth_header(admin_user))
    assert resp.json()["service_fee_rate"] == "8.00"


async def test_baked_order_unaffected_by_later_global_change(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """烙盘保护：全局费率改动不影响已发布订单的结算。"""
    await _set_global_rate(client, admin_user, "8")
    await _set_individual_fee_enabled(client, admin_user, True)

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
    await _set_individual_fee_enabled(client, admin_user, True)

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

    # 后台总开关保持开启时，逐单显式关闭应清零
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
    await _set_individual_fee_enabled(client, admin_user, True)

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


async def test_non_admin_publish_bakes_global_rate_like_admin(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session: AsyncSession,
):
    """普通用户发单与管理员一致：总开关关闭时按全局费率烙盘（2026-10-11 修复）。

    修复前服务端对非管理员发布者一律返回 None，导致全局服务费设置只对
    老板自己的账号生效——其他用户发单既没有逐单选项、也不收服务费。
    """
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

    # 总开关关闭：客户端传来的逐单字段被忽略，按全局 8% 烙盘
    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=True, service_fee_rate="50"),
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["service_fee_rate"] == "8.00"
    assert order["net_amount"] == "138.00"

    # 编辑自己的单同样按当前全局重新烙盘（逐单字段仍被忽略）
    resp = await client.put(
        f"/orders/{order['id']}",
        json={"service_fee_enabled": True, "service_fee_rate": "50"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == "8.00"


async def test_non_admin_publish_respects_individual_switch(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session: AsyncSession,
):
    """总开关开启后，普通用户发单同样可逐单关闭/自定义费率（与管理员一致）。"""
    from app.models.wallet import WalletTransactionType
    from app.services.wallet_service import WalletService

    await _set_global_rate(client, admin_user, "8")
    await _set_individual_fee_enabled(client, admin_user, True)

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

    # 留空费率 → 按全局 8%
    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=True),
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 201
    assert resp.json()["service_fee_rate"] == "8.00"

    # 手填费率 → 逐单优先
    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=True, service_fee_rate="20"),
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 201
    assert resp.json()["service_fee_rate"] == "20.00"

    # 显式关闭 → 本单不收
    resp = await client.post(
        "/orders/create",
        json=_order_payload(service_fee_enabled=False),
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 201
    assert resp.json()["service_fee_rate"] == "0.00"
    assert resp.json()["net_amount"] == "150.00"


async def test_public_service_fee_settings_readable_by_any_user(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
) -> None:
    """发单页读取全局服务费设置的公共端点：任何登录用户 200，写仍 403。"""
    await _set_global_rate(client, admin_user, "8")

    resp = await client.get(
        "/service-fee/settings", headers=auth_header(registered_user)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["service_fee_rate"] == "8.00"
    assert body["individual_service_fee_enabled"] is False

    # 未登录不可读
    resp = await client.get("/service-fee/settings")
    assert resp.status_code == 401

    # 公共端点只读：普通用户不能通过它改设置
    resp = await client.put(
        "/service-fee/settings",
        json={"service_fee_rate": "50"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 405
