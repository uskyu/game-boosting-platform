"""Regression tests for order dispatch controls and extended fields."""
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient
from tests.conftest import auth_header


async def _create(client: AsyncClient, user: dict, **extra) -> dict:
    payload = {"game_name": "王者荣耀", "current_rank": "钻石", "target_rank": "王者", "price": "500.00"}
    payload.update(extra)
    response = await client.post("/orders/create", json=payload, headers=auth_header(user))
    assert response.status_code == 201
    return response.json()


async def test_extended_order_fields_and_default_claim_limit(client: AsyncClient, admin_user: dict):
    order = await _create(client, admin_user, title="上分订单", intro="简介", price_min="300.00", price_max="500.00")
    assert order["title"] == "上分订单"
    assert order["price_min"] == "300.00"
    assert order["price_max"] == "500.00"
    assert order["max_claims"] == 1 and order["claimed_count"] == 0
    assert order["claim_status"] == "OPEN"


async def test_admin_can_pause_resume_and_archive(client: AsyncClient, admin_user: dict):
    order = await _create(client, admin_user)
    for action, expected in (("pause", "PAUSED"), ("resume", "OPEN"), ("close", "CLOSED")):
        response = await client.put(f"/orders/{order['id']}/claim-control", json={"action": action}, headers=auth_header(admin_user))
        assert response.status_code == 200
        assert response.json()["claim_status"] == expected
    response = await client.put(f"/orders/{order['id']}/claim-control", json={"action": "archive"}, headers=auth_header(admin_user))
    assert response.status_code == 200 and response.json()["is_archived"] is True


async def test_booster_cannot_control_order(client: AsyncClient, admin_user: dict, booster_user: dict):
    order = await _create(client, admin_user)
    response = await client.put(f"/orders/{order['id']}/claim-control", json={"action": "pause"}, headers=auth_header(booster_user))
    assert response.status_code == 403


async def test_delete_claimed_order_is_protected(client: AsyncClient, admin_user: dict, booster_user: dict):
    order = await _create(client, admin_user)
    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))
    response = await client.delete(f"/orders/{order['id']}", headers=auth_header(admin_user))
    assert response.status_code == 409
