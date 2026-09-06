"""Push subscription API coverage."""

import pytest

from tests.conftest import auth_header

SUBSCRIPTION = {
    "endpoint": "https://push.example.test/subscription-a",
    "p256dh": "p256dh-original",
    "auth": "auth-original",
}


@pytest.mark.asyncio
async def test_push_endpoints_require_authentication(client):
    assert (await client.get("/push/public-key")).status_code == 200
    assert (await client.post("/push/subscribe", json=SUBSCRIPTION)).status_code == 401
    assert (await client.request("DELETE", "/push/subscribe", json={"endpoint": SUBSCRIPTION["endpoint"]})).status_code == 401


@pytest.mark.asyncio
async def test_public_key_is_safe_when_vapid_is_missing(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PUSH_VAPID_PUBLIC_KEY", None)
    response = await client.get("/push/public-key")
    assert response.status_code in (200, 401)
    if response.status_code == 200:
        assert response.json().get("enabled") is False or not response.json().get("public_key")


@pytest.mark.asyncio
async def test_authenticated_user_can_subscribe_and_verify_or_repost(client, registered_user):
    headers = auth_header(registered_user)
    response = await client.post("/push/subscribe", json=SUBSCRIPTION, headers=headers)
    assert response.status_code in (200, 201)
    assert response.json()["endpoint"] == SUBSCRIPTION["endpoint"]
    assert response.json()["enabled"] is True

    listed = await client.get("/push/subscribe", headers=headers)
    if listed.status_code != 404:
        assert listed.status_code == 200
        assert any(item["endpoint"] == SUBSCRIPTION["endpoint"] for item in listed.json())
    else:
        duplicate = await client.post("/push/subscribe", json=SUBSCRIPTION, headers=headers)
        assert duplicate.status_code in (200, 201)


@pytest.mark.asyncio
async def test_user_cannot_delete_another_users_endpoint(client, registered_user, booster_user):
    endpoint = {"endpoint": SUBSCRIPTION["endpoint"]}
    assert (await client.post("/push/subscribe", json=SUBSCRIPTION, headers=auth_header(registered_user))).status_code in (200, 201)
    response = await client.request("DELETE", "/push/subscribe", json=endpoint, headers=auth_header(booster_user))
    assert response.status_code in (403, 404)


@pytest.mark.asyncio
async def test_reposting_endpoint_updates_keys(client, registered_user):
    headers = auth_header(registered_user)
    first = await client.post("/push/subscribe", json=SUBSCRIPTION, headers=headers)
    assert first.status_code in (200, 201)
    updated = {**SUBSCRIPTION, "p256dh": "p256dh-updated", "auth": "auth-updated"}
    second = await client.post("/push/subscribe", json=updated, headers=headers)
    assert second.status_code in (200, 201)
    assert second.json()["endpoint"] == SUBSCRIPTION["endpoint"]
    assert second.json()["enabled"] is True

    from sqlalchemy import select
    from app.models.push_subscription import PushSubscription
    from app.models.user import User
    from tests.conftest import _session_factory

    async with _session_factory() as db:
        user = (await db.execute(select(User).where(User.email == "testuser@example.com"))).scalar_one()
        subscriptions = (await db.execute(select(PushSubscription).where(PushSubscription.user_id == user.id))).scalars().all()
        assert len(subscriptions) == 1
        assert subscriptions[0].p256dh == "p256dh-updated"
        assert subscriptions[0].auth == "auth-updated"
