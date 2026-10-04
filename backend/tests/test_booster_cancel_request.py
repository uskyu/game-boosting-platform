"""Booster cancellation requests are reviewed by admins before cancellation."""

from httpx import AsyncClient

from tests.conftest import auth_header


async def _create_order(client: AsyncClient, admin_user: dict) -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
        },
        headers=auth_header(admin_user),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_booster_cancel_request_waits_for_admin_and_keeps_order_record(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
):
    order = await _create_order(client, admin_user)
    order_id = order["id"]

    accepted = await client.put(
        f"/orders/{order_id}/accept",
        headers=auth_header(booster_user),
    )
    assert accepted.status_code == 200, accepted.text

    request = await client.post(
        f"/orders/{order_id}/request-cancel",
        json={"reason": "临时无法继续履约"},
        headers=auth_header(booster_user),
    )
    assert request.status_code == 200, request.text
    assert request.json()["status"] == "DISPUTED"
    assert "打手取消申请" in request.json()["notes"]
    assert "临时无法继续履约" in request.json()["notes"]

    # A booster request is not an immediate cancellation. The administrator
    # can decide directly without asking the booster to consent.
    detail = await client.get(
        f"/orders/{order_id}",
        headers=auth_header(booster_user),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "DISPUTED"

    decision = await client.put(
        f"/admin/orders/{order_id}/intervene",
        json={"action": "CANCELLED", "reason": "管理员裁决取消"},
        headers=auth_header(admin_user),
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "CANCELLED"

    # Cancellation keeps the original order available in the order list and
    # detail route so the parties can review its order information.
    listing = await client.get(
        "/orders/",
        params={"status": "CANCELLED"},
        headers=auth_header(admin_user),
    )
    assert listing.status_code == 200, listing.text
    assert any(item["id"] == order_id for item in listing.json()["items"])

    final_detail = await client.get(
        f"/orders/{order_id}",
        headers=auth_header(booster_user),
    )
    assert final_detail.status_code == 200, final_detail.text
    assert final_detail.json()["status"] == "CANCELLED"


async def test_publisher_cannot_review_claim_while_cancel_request_is_pending(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    owner_response = await client.post(
        "/auth/register",
        json={
            "email": "cancel-request-owner@example.com",
            "username": "CancelRequestOwner",
            "password": "TestPass123",
            **make_captcha(),
        },
    )
    assert owner_response.status_code in (200, 201), owner_response.text
    owner = await client.post(
        "/auth/login",
        json={"email": "cancel-request-owner@example.com", "password": "TestPass123"},
    )
    assert owner.status_code == 200, owner.text
    owner_user_id = owner.json()["user"]["id"]

    funded = await client.post(
        f"/admin/wallets/{owner_user_id}/adjust",
        json={"amount": 200, "reason": "test fund"},
        headers=auth_header(admin_user),
    )
    assert funded.status_code in (200, 201), funded.text

    order_response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
        },
        headers=auth_header(owner.json()),
    )
    assert order_response.status_code == 201, order_response.text
    order_id = order_response.json()["id"]

    accepted = await client.put(
        f"/orders/{order_id}/accept",
        headers=auth_header(booster_user),
    )
    assert accepted.status_code == 200, accepted.text
    delivered = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "已完成"},
        headers=auth_header(booster_user),
    )
    assert delivered.status_code == 200, delivered.text

    claims = await client.get(
        f"/orders/{order_id}/claims",
        headers=auth_header(owner.json()),
    )
    assert claims.status_code == 200, claims.text
    claim_id = claims.json()["items"][0]["id"]

    request = await client.post(
        f"/orders/{order_id}/request-cancel",
        json={"reason": "无法继续履约"},
        headers=auth_header(booster_user),
    )
    assert request.status_code == 200, request.text
    assert request.json()["status"] == "DISPUTED"

    review = await client.put(
        f"/orders/{order_id}/claims/{claim_id}/review",
        json={"action": "approve"},
        headers=auth_header(owner.json()),
    )
    assert review.status_code == 400, review.text
    assert "等待管理员裁决" in review.json()["detail"]


async def test_disputed_order_claim_cannot_be_auto_settled():
    from types import SimpleNamespace

    from app.models.order import ClaimLifecycleStatus, OrderStatus
    from app.services.order_service import OrderService

    service = OrderService(None)
    order = SimpleNamespace(status=OrderStatus.DISPUTED)
    claim = SimpleNamespace(status=ClaimLifecycleStatus.DELIVERED)
    assert await service.auto_settle_due_claim(order, claim) is False
