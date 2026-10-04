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
