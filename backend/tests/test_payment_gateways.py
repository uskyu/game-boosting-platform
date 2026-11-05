"""支付网关管理接口测试：新增 / 修改 / 删除 / 切换 / 下单走当前网关 / 补单。

覆盖重点：
- 只填域名被拒绝（线上实测根路径下可能有另一个商户入口）；
- 勾选自动补 .php 与填写完整入口两种模式；
- 当前使用的唯一性；当前使用/被引用不能删除；
- 下单落到当前网关，订单记录 gateway_id 与网关名快照；
- 没有查询地址的网关，补单返回 UNSUPPORTED 且不动订单状态。
"""

from httpx import AsyncClient

from tests.conftest import auth_header

FULL_ENDPOINT = "https://api.example.com/gateway/submit"
EPAY_BASE = "https://pay.example.com/xpay/epay"


def _payload(**overrides) -> dict:
    data = {
        "name": "立收通",
        "submit_url": FULL_ENDPOINT,
        "append_submit_php": False,
        "merchant_id": "2849678331136704512",
        "secret_key": "unit-test-secret",
        "query_url": None,
        "alipay_enabled": True,
        "wxpay_enabled": True,
        "enabled": True,
    }
    data.update(overrides)
    return data


async def _create(client: AsyncClient, admin_user: dict, **overrides) -> dict:
    resp = await client.post(
        "/admin/payment/gateways",
        json=_payload(**overrides),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_first_gateway_becomes_active(client: AsyncClient, admin_user: dict):
    created = await _create(client, admin_user)
    assert created["is_active"] is True
    assert created["resolved_submit_url"] == FULL_ENDPOINT
    # 不补 .php 的网关不臆造查询地址
    assert created["supports_query"] is False
    assert "secret_key" not in created


async def test_epay_gateway_gets_submit_php_and_query_url(
    client: AsyncClient, admin_user: dict
):
    created = await _create(
        client,
        admin_user,
        submit_url=EPAY_BASE,
        append_submit_php=True,
    )
    assert created["resolved_submit_url"] == f"{EPAY_BASE}/submit.php"
    assert created["supports_query"] is True


async def test_domain_only_address_is_rejected(client: AsyncClient, admin_user: dict):
    resp = await client.post(
        "/admin/payment/gateways",
        json=_payload(submit_url="https://api.example.com", append_submit_php=False),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400
    assert "缺少支付路径" in resp.json()["detail"]


async def test_switching_active_gateway(client: AsyncClient, admin_user: dict):
    first = await _create(client, admin_user)
    second = await _create(client, admin_user, name="马支付", submit_url=EPAY_BASE, append_submit_php=True)
    assert second["is_active"] is False

    resp = await client.post(
        f"/admin/payment/gateways/{second['id']}/activate",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    settings = (
        await client.get("/admin/payment/settings", headers=auth_header(admin_user))
    ).json()
    assert settings["active_gateway_id"] == second["id"]
    by_id = {item["id"]: item for item in settings["gateways"]}
    assert by_id[first["id"]]["is_active"] is False


async def test_active_gateway_cannot_be_deleted(client: AsyncClient, admin_user: dict):
    created = await _create(client, admin_user)
    resp = await client.delete(
        f"/admin/payment/gateways/{created['id']}",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400
    assert "不能删除" in resp.json()["detail"]


async def test_inactive_gateway_can_be_deleted(client: AsyncClient, admin_user: dict):
    await _create(client, admin_user)
    extra = await _create(client, admin_user, name="备用", submit_url=EPAY_BASE)
    resp = await client.delete(
        f"/admin/payment/gateways/{extra['id']}",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 204


async def test_recharge_uses_active_gateway(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await client.put(
        "/admin/payment/settings",
        json={"enabled": True, "min_amount": 1},
        headers=auth_header(admin_user),
    )
    gateway = await _create(client, admin_user)

    resp = await client.post(
        "/wallet/recharge",
        json={"amount": 10, "payment_method": "alipay"},
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["pay_url"] == FULL_ENDPOINT

    listed = (
        await client.get("/wallet/recharge/mine", headers=auth_header(registered_user))
    ).json()
    # 用户侧列表不带网关字段，这里回后台接口确认网关落库
    recharges = (
        await client.get("/admin/payment/recharges", headers=auth_header(admin_user))
    ).json()
    order = next(item for item in recharges["items"] if item["trade_no"] == listed["items"][0]["trade_no"])
    assert order["gateway_id"] == gateway["id"]
    assert order["gateway_name"] == gateway["name"]


async def test_sync_reports_unsupported_without_query_url(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await client.put(
        "/admin/payment/settings",
        json={"enabled": True, "min_amount": 1},
        headers=auth_header(admin_user),
    )
    await _create(client, admin_user)  # 不补 .php，没有查询地址

    await client.post(
        "/wallet/recharge",
        json={"amount": 10, "payment_method": "alipay"},
        headers=auth_header(registered_user),
    )
    recharges = (
        await client.get("/admin/payment/recharges", headers=auth_header(admin_user))
    ).json()
    order = recharges["items"][0]

    resp = await client.post(
        f"/admin/payment/recharges/{order['id']}/sync",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "UNSUPPORTED"
    assert body["order"]["status"] == "PENDING"
