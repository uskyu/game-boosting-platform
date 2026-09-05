import pytest
from httpx import AsyncClient

from tests.conftest import auth_header

VALID_PAYLOAD = {"game_name": "王者荣耀", "title": "上分", "price": "500.00"}


async def create_template(client: AsyncClient, user: dict, name: str = "常用模板", payload: dict | None = None) -> dict:
    response = await client.post("/order-templates", json={"name": name, "payload": payload or VALID_PAYLOAD}, headers=auth_header(user))
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("method", ["get", "post"])
async def test_order_templates_require_authentication(client: AsyncClient, method: str):
    response = await (client.post("/order-templates", json={"name": "模板", "payload": VALID_PAYLOAD}) if method == "post" else client.get("/order-templates"))
    assert response.status_code == 401


async def test_user_can_create_list_get_update_and_delete_template(client: AsyncClient, registered_user: dict):
    headers = auth_header(registered_user)
    created = await create_template(client, registered_user)
    assert created["name"] == "常用模板"
    assert created["payload"]["price"] == "500.00"
    listed = await client.get("/order-templates", headers=headers)
    assert listed.status_code == 200 and any(item["id"] == created["id"] for item in listed.json())
    detail = await client.get(f"/order-templates/{created['id']}", headers=headers)
    assert detail.status_code == 200 and detail.json() == created
    updated = await client.patch(f"/order-templates/{created['id']}", json={"name": "更新模板", "payload": {**VALID_PAYLOAD, "price": "800.00"}}, headers=headers)
    assert updated.status_code == 200 and updated.json()["payload"]["price"] == "800.00"
    assert (await client.delete(f"/order-templates/{created['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"/order-templates/{created['id']}", headers=headers)).status_code == 404


async def test_template_isolation(client: AsyncClient, registered_user: dict, booster_user: dict):
    template = await create_template(client, registered_user)
    headers = auth_header(booster_user)
    assert (await client.get(f"/order-templates/{template['id']}", headers=headers)).status_code == 404
    assert (await client.patch(f"/order-templates/{template['id']}", json={"name": "越权"}, headers=headers)).status_code == 404
    assert (await client.delete(f"/order-templates/{template['id']}", headers=headers)).status_code == 404


async def test_partial_template_and_blank_values_are_normalized(client: AsyncClient, registered_user: dict):
    template = await create_template(client, registered_user, name="  部分模板  ", payload={"game_name": "  王者荣耀  ", "title": "  "})
    assert template["name"] == "部分模板"
    assert template["payload"] == {"game_name": "王者荣耀"}


@pytest.mark.parametrize("body", [{"name": None}, {"payload": None}])
async def test_patch_null_fields_rejected(client: AsyncClient, registered_user: dict, body: dict):
    template = await create_template(client, registered_user)
    response = await client.patch(f"/order-templates/{template['id']}", json=body, headers=auth_header(registered_user))
    assert response.status_code == 422


@pytest.mark.parametrize("body", [
    {"name": "模板", "payload": {**VALID_PAYLOAD, "fields": {}}},
    {"name": "模板", "payload": {**VALID_PAYLOAD, "attachments": []}},
    {"name": "模板", "payload": {**VALID_PAYLOAD, "password": "secret"}},
    {"name": "", "payload": VALID_PAYLOAD},
    {"name": "   ", "payload": VALID_PAYLOAD},
    {"name": "模板", "payload": {"game_name": "  ", "title": "  "}},
    {"name": "模板", "payload": {**VALID_PAYLOAD, "price": "0"}},
    {"name": "模板", "payload": {**VALID_PAYLOAD, "price": "-1"}},
    {"name": "模板", "payload": {**VALID_PAYLOAD, "price": "not-a-price"}},
])
async def test_order_template_validation_returns_422(client: AsyncClient, registered_user: dict, body: dict):
    response = await client.post("/order-templates", json=body, headers=auth_header(registered_user))
    assert response.status_code == 422
