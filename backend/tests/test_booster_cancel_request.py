"""打手发起取消协商后由发单员直接同意/拒绝，不经过管理员。"""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import ClaimLifecycleStatus, Order, OrderClaim, OrderStatus
from app.models.user import User
from app.models.wallet import Wallet
from app.services.order_service import OrderService
from tests.conftest import auth_header


async def _register(client: AsyncClient, make_captcha) -> dict:
    response = await client.post(
        "/auth/register",
        json={
            "email": "cancel-request-owner@example.com",
            "username": "CancelRequestOwner",
            "password": "TestPass123",
            **make_captcha(),
        },
    )
    assert response.status_code in (200, 201), response.text
    login = await client.post(
        "/auth/login",
        json={"email": "cancel-request-owner@example.com", "password": "TestPass123"},
    )
    assert login.status_code == 200, login.text
    return login.json()


async def _fund(client: AsyncClient, admin_user: dict, user: dict) -> None:
    response = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": 200, "reason": "test fund"},
        headers=auth_header(admin_user),
    )
    assert response.status_code in (200, 201), response.text


async def _deposit(client: AsyncClient, admin_user: dict, booster: dict) -> None:
    response = await client.post(
        f"/admin/users/{booster['user']['id']}/adjust-deposit",
        json={"delta": 50, "remark": "test deposit"},
        headers=auth_header(admin_user),
    )
    assert response.status_code in (200, 201), response.text


async def _create_order(client: AsyncClient, publisher: dict) -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
        },
        headers=auth_header(publisher),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _active_claim_id(client: AsyncClient, publisher: dict, order_id: int, booster_id: int) -> int:
    response = await client.get(f"/orders/{order_id}/claims", headers=auth_header(publisher))
    assert response.status_code == 200, response.text
    return next(
        item["id"] for item in response.json()["items"] if item["booster_id"] == booster_id
    )


async def test_booster_request_can_be_rejected_then_approved_without_admin(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    publisher = await _register(client, make_captcha)
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher)
    await _deposit(client, admin_user, booster_user)
    order = await _create_order(client, publisher)
    accepted = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(booster_user)
    )
    assert accepted.status_code == 200, accepted.text
    claim_id = await _active_claim_id(client, publisher, order["id"], booster_id)

    # 老管理员裁决 URL 被停用，不能再将订单标成 DISPUTED。
    legacy = await client.post(
        f"/orders/{order['id']}/request-cancel",
        json={"reason": "旧流程不再使用"},
        headers=auth_header(booster_user),
    )
    assert legacy.status_code == 409, legacy.text

    request_response = await client.post(
        f"/orders/{order['id']}/cancel-requests",
        json={"claim_id": claim_id, "reason": "无法继续履约", "compensation_amount": 20},
        headers=auth_header(booster_user),
    )
    assert request_response.status_code == 200, request_response.text
    request = request_response.json()
    assert request["requester_role"] == "BOOSTER"
    assert request["status"] == "PENDING"

    visible = await client.get(
        f"/orders/{order['id']}/cancel-requests", headers=auth_header(publisher)
    )
    assert visible.status_code == 200, visible.text
    assert visible.json()["items"][0]["reason"] == "无法继续履约"
    assert visible.json()["items"][0]["compensation_amount"] == "20.00"

    forbidden = await client.post(
        f"/orders/{order['id']}/cancel-requests/{request['id']}/decision",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert forbidden.status_code == 403, forbidden.text

    rejected = await client.post(
        f"/orders/{order['id']}/cancel-requests/{request['id']}/decision",
        json={"action": "reject", "decision_reason": "请先补齐交付说明"},
        headers=auth_header(publisher),
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"
    order_response = await client.get(f"/orders/{order['id']}", headers=auth_header(publisher))
    assert order_response.json()["status"] == "LOCKED"
    db_session.expire_all()
    claim = await db_session.get(OrderClaim, claim_id)
    assert claim.status == ClaimLifecycleStatus.CLAIMED
    wallet = await db_session.execute(select(Wallet).where(Wallet.user_id == booster_id))
    assert wallet.scalar_one().deposit_balance == Decimal("50.00")
    publisher_wallet = await db_session.execute(select(Wallet).where(Wallet.user_id == publisher_id))
    assert publisher_wallet.scalar_one().available_balance == Decimal("100.00")
    # Other HTTP requests commit in separate sessions; end this read snapshot
    # before checking the later accepted request.
    await db_session.rollback()

    # 被拒绝后可按新金额重提；同意才会取消并划转赔偿。
    retry = await client.post(
        f"/orders/{order['id']}/cancel-requests",
        json={"claim_id": claim_id, "reason": "双方重新谈妥补偿", "compensation_amount": 10},
        headers=auth_header(booster_user),
    )
    assert retry.status_code == 200, retry.text
    accepted_request = await client.post(
        f"/orders/{order['id']}/cancel-requests/{retry.json()['id']}/decision",
        json={"action": "approve"},
        headers=auth_header(publisher),
    )
    assert accepted_request.status_code == 200, accepted_request.text
    assert accepted_request.json()["status"] == "APPROVED"
    final_order = await client.get(f"/orders/{order['id']}", headers=auth_header(publisher))
    assert final_order.json()["status"] == "CANCELLED"
    db_session.expire_all()
    claim = await db_session.get(OrderClaim, claim_id)
    assert claim.status == ClaimLifecycleStatus.CANCELLED
    wallet = await db_session.execute(select(Wallet).where(Wallet.user_id == booster_id))
    assert wallet.scalar_one().deposit_balance == Decimal("40.00")


async def test_pending_cancel_request_blocks_delivery_review(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(client, make_captcha)
    await _fund(client, admin_user, publisher)
    order = await _create_order(client, publisher)
    accepted = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(booster_user)
    )
    assert accepted.status_code == 200, accepted.text
    delivered = await client.put(
        f"/orders/{order['id']}/deliver",
        json={"delivery_note": "已完成"},
        headers=auth_header(booster_user),
    )
    assert delivered.status_code == 200, delivered.text
    claim_id = delivered.json()["my_claim"]["id"]

    request = await client.post(
        f"/orders/{order['id']}/cancel-requests",
        json={"claim_id": claim_id, "reason": "先协商后决定是否继续", "compensation_amount": 0},
        headers=auth_header(publisher),
    )
    assert request.status_code == 200, request.text
    dispute = await client.put(
        f"/orders/{order['id']}/dispute",
        headers=auth_header(publisher),
    )
    assert dispute.status_code == 409, dispute.text
    assert "取消协商" in dispute.json()["detail"]

    review = await client.put(
        f"/orders/{order['id']}/claims/{claim_id}/review",
        json={"action": "approve"},
        headers=auth_header(publisher),
    )
    assert review.status_code == 409, review.text
    assert "取消协商" in review.json()["detail"]


@pytest.mark.asyncio
async def test_disputed_order_claim_cannot_be_auto_settled():
    from app.models.order import ClaimLifecycleStatus as Lifecycle, OrderStatus as Status

    service = OrderService(None)
    order = SimpleNamespace(status=Status.DISPUTED, id=1)
    claim = SimpleNamespace(status=Lifecycle.DELIVERED, id=1)
    assert await service.auto_settle_due_claim(order, claim) is False
