"""Conversation pinning behavior tests."""

from httpx import AsyncClient

from tests.conftest import auth_header


async def test_admin_conversation_is_pinned_for_regular_user(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
) -> None:
    response = await client.post(
        "/chat/conversations",
        headers=auth_header(registered_user),
        json={"target_user_id": admin_user["user"]["id"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["is_pinned"] is True
    current = next(p for p in data["participants"] if p["user_id"] == registered_user["user"]["id"])
    admin = next(p for p in data["participants"] if p["user_id"] == admin_user["user"]["id"])
    assert current["is_pinned"] is True
    assert current["pinned_at"] is not None
    assert admin["is_pinned"] is False


async def test_participant_can_pin_and_unpin_conversation(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
) -> None:
    created = await client.post(
        "/chat/conversations",
        headers=auth_header(registered_user),
        json={"target_user_id": booster_user["user"]["id"]},
    )
    conversation_id = created.json()["id"]

    pinned = await client.put(
        f"/chat/conversations/{conversation_id}/pin",
        headers=auth_header(registered_user),
    )
    assert pinned.status_code == 200
    assert pinned.json()["is_pinned"] is True

    unpinned = await client.delete(
        f"/chat/conversations/{conversation_id}/pin",
        headers=auth_header(registered_user),
    )
    assert unpinned.status_code == 200
    assert unpinned.json()["is_pinned"] is False
    assert unpinned.json()["pinned_at"] is None


async def test_non_participant_cannot_pin_conversation(
    client: AsyncClient,
    registered_user: dict,
    booster_user: dict,
    admin_user: dict,
) -> None:
    created = await client.post(
        "/chat/conversations",
        headers=auth_header(registered_user),
        json={"target_user_id": booster_user["user"]["id"]},
    )
    conversation_id = created.json()["id"]

    response = await client.put(
        f"/chat/conversations/{conversation_id}/pin",
        headers=auth_header(admin_user),
    )
    assert response.status_code == 403
