"""Endpoint timing contract checks for order create/accept flows.

These tests use the existing local test DB fixtures; no production endpoint is
contacted. They verify fixed labels/stage names and avoid asserting wall time.
"""

import logging
import re

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import OrderClaim
from app.models.user import User
from tests.conftest import auth_header


def _timing_items(response) -> list[str]:
    value = response.headers.get("server-timing", "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _assert_has_stage(response, stage: str) -> None:
    items = _timing_items(response)
    assert any(re.fullmatch(r"response_start;dur=\d+(?:\.\d+)?", item) for item in items)
    assert any(re.fullmatch(rf"{re.escape(stage)};dur=\d+(?:\.\d+)?", item) for item in items)


async def _top_up(client: AsyncClient, admin_user: dict, user_id: int, amount: int) -> None:
    response = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": amount, "reason": "local timing test"},
        headers=auth_header(admin_user),
    )
    assert response.status_code in (200, 201), response.text


async def test_create_endpoint_exposes_fixed_timing_only(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session: AsyncSession,
    caplog,
) -> None:
    publisher_id = registered_user["user"]["id"]
    await _top_up(client, admin_user, publisher_id, 500)
    await db_session.commit()

    caplog.set_level(logging.INFO, logger="app.core.request_timing")
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "description_raw": "timing integration order",
            "boss_contact": "LOCAL-TEST",
        },
        headers=auth_header(registered_user),
    )
    assert response.status_code == 201, response.text
    _assert_has_stage(response, "order_service")
    _assert_has_stage(response, "order_notifications")

    timing_records = [
        record for record in caplog.records
        if getattr(record, "event", None) == "request_timing"
        and getattr(record, "route", None) == "/api/v1/orders/create"
    ]
    assert timing_records
    message = timing_records[-1].getMessage()
    assert f"/orders/{response.json()['id']}" not in message
    assert "order_id=" not in message
    assert registered_user["user"]["email"] not in message
    assert "timing integration order" not in message


async def test_accept_endpoint_exposes_fixed_timing_without_order_id_label(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    caplog,
) -> None:
    publisher_id = registered_user["user"]["id"]
    booster_id = booster_user["user"]["id"]
    await _top_up(client, admin_user, publisher_id, 500)
    await _top_up(client, admin_user, booster_id, 500)
    await db_session.commit()

    created = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "description_raw": "accept timing integration order",
            "boss_contact": "LOCAL-TEST",
        },
        headers=auth_header(registered_user),
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    caplog.set_level(logging.INFO, logger="app.core.request_timing")
    accepted = await client.put(
        f"/orders/{order_id}/accept",
        headers=auth_header(booster_user),
    )
    assert accepted.status_code == 200, accepted.text
    _assert_has_stage(accepted, "accept_service")
    _assert_has_stage(accepted, "accept_side_effects")

    timing_records = [
        record for record in caplog.records
        if getattr(record, "event", None) == "request_timing"
        and getattr(record, "route", None) == "/api/v1/orders/{order_id}/accept"
    ]
    assert timing_records
    message = timing_records[-1].getMessage()
    assert f"/orders/{order_id}/accept" not in message
    assert "order_id=" not in message
    assert booster_user["user"]["email"] not in message
    assert "accept timing integration order" not in message

    claim_result = await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order_id)
    )
    assert claim_result.scalar_one().booster_id == booster_id
