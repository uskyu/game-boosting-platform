"""已取消订单也可申请售后（老板 2026-10-22 需求）。

覆盖：取消单发起成功、未接单的取消单拒绝、进行中订单仍拒绝、
管理员裁定通过后按「可用余额 → 保证金」扣款划给发布者。
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order_after_sale import AfterSaleStatus, OrderAfterSale
from app.models.wallet import Wallet
from tests.conftest import auth_header


async def _fund_wallet(client: AsyncClient, admin_user: dict, user_id: int, amount: str) -> None:
    response = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": amount, "reason": "测试入账"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text


async def _create_order(client: AsyncClient, publisher: dict, price: str = "100.00") -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
        },
        headers=auth_header(publisher),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _cancel_order(client: AsyncClient, admin_user: dict, order_id: int) -> None:
    response = await client.put(
        f"/admin/orders/{order_id}/intervene",
        json={"action": "CANCELLED", "reason": "测试取消"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANCELLED"


async def test_cancelled_order_with_booster_can_apply(
    client: AsyncClient, admin_user: dict, registered_user: dict, booster_user: dict
) -> None:
    await _fund_wallet(client, admin_user, registered_user["user"]["id"], "200.00")
    order = await _create_order(client, registered_user)
    accepted = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(booster_user)
    )
    assert accepted.status_code == 200, accepted.text

    await _cancel_order(client, admin_user, order["id"])

    response = await client.post(
        f"/orders/{order['id']}/after-sales",
        json={"reason": "订单取消但前期工作未完成，要求补偿", "amount": "30.00", "images": []},
        headers=auth_header(registered_user),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING"
    assert Decimal(str(body["amount"])) == Decimal("30.00")


async def test_cancelled_order_without_booster_rejected(
    client: AsyncClient, admin_user: dict, registered_user: dict
) -> None:
    await _fund_wallet(client, admin_user, registered_user["user"]["id"], "200.00")
    order = await _create_order(client, registered_user)
    await _cancel_order(client, admin_user, order["id"])

    response = await client.post(
        f"/orders/{order['id']}/after-sales",
        json={"reason": "没有打手的取消单不该能售后", "amount": "10.00", "images": []},
        headers=auth_header(registered_user),
    )
    assert response.status_code == 400
    assert "没有接单打手" in response.json()["detail"]


async def test_in_progress_order_still_rejected(
    client: AsyncClient, admin_user: dict, registered_user: dict
) -> None:
    await _fund_wallet(client, admin_user, registered_user["user"]["id"], "200.00")
    order = await _create_order(client, registered_user)
    response = await client.post(
        f"/orders/{order['id']}/after-sales",
        json={"reason": "进行中订单不支持售后", "amount": "10.00", "images": []},
        headers=auth_header(registered_user),
    )
    assert response.status_code == 400
    assert "已完成或已取消" in response.json()["detail"]


async def test_cancelled_order_after_sale_approval_transfers_funds(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
) -> None:
    await _fund_wallet(client, admin_user, registered_user["user"]["id"], "200.00")
    order = await _create_order(client, registered_user)
    accepted = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(booster_user)
    )
    assert accepted.status_code == 200, accepted.text
    await _cancel_order(client, admin_user, order["id"])

    created = await client.post(
        f"/orders/{order['id']}/after-sales",
        json={"reason": "取消单售后扣款验证", "amount": "30.00", "images": []},
        headers=auth_header(registered_user),
    )
    assert created.status_code == 201, created.text
    after_sale_id = created.json()["id"]

    # 打手账上先有钱，才能扣款
    await _fund_wallet(client, admin_user, booster_user["user"]["id"], "50.00")

    reviewed = await client.post(
        f"/admin/after-sales/{after_sale_id}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "APPROVED"

    rows = await db_session.execute(
        select(OrderAfterSale).where(OrderAfterSale.id == after_sale_id)
    )
    assert rows.scalar_one().status == AfterSaleStatus.APPROVED

    booster_wallet = (
        await db_session.execute(
            select(Wallet).where(Wallet.user_id == booster_user["user"]["id"])
        )
    ).scalar_one()
    assert Decimal(str(booster_wallet.available_balance)) == Decimal("20.00")
