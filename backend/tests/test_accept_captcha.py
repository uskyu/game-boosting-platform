"""接单图形验证码测试（2026-10-22）。

覆盖后台开关「接单需要验证码」的两套契约：

1. 开关关闭（默认）：接单保持旧行为，请求可以完全不带 body；
2. 开关开启：必须携带 accept 场景的 captcha_id/captcha_code，缺失/错误/跨场景
   一律返回 400 且 detail 为 ``{"code": "captcha_invalid", ...}``；
3. ``GET /orders/accept-captcha`` 关闭时 400、开启时返回 captcha_id + base64 图片；
4. 订单详情按开关回填 ``accept_captcha_required``，供前端决定是否先弹验证码。

验证码是一次性消费的：每个需要「正确码」的用例都现场调用
``captcha_service.create("accept")`` 生成，不能复用。
"""

from httpx import AsyncClient
from sqlalchemy import select

from app.models.site_setting import SiteSetting
from app.services import captcha_service
from tests.conftest import auth_header


async def _set_accept_captcha(db_session, enabled: bool) -> None:
    """直接改设置行（conftest 每个用例已插入 id=1 的默认行）。"""
    row = (
        await db_session.execute(select(SiteSetting).where(SiteSetting.id == 1))
    ).scalar_one()
    row.accept_captcha_enabled = enabled
    await db_session.commit()


def _make_accept_captcha() -> dict:
    """生成 accept 场景验证码并取出明文（仅测试可用）。"""
    captcha_id, _ = captcha_service.create("accept")
    code = captcha_service._store[captcha_id][0]
    return {"captcha_id": captcha_id, "captcha_code": code}


async def _create_order(client: AsyncClient, publisher: dict) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "title": "接单验证码测试",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "200.00",
            "max_claims": 1,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _accept(client: AsyncClient, order_id: int, booster: dict, json=None):
    return await client.put(
        f"/orders/{order_id}/accept",
        headers=auth_header(booster),
        **({"json": json} if json is not None else {}),
    )


async def test_accept_without_captcha_when_disabled(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """开关关闭（默认）：接单不需要 body，保持现状。"""
    await _set_accept_captcha(db_session, False)
    order = await _create_order(client, admin_user)

    resp = await _accept(client, order["id"], booster_user)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "LOCKED"
    assert data["booster_id"] == booster_user["user"]["id"]


async def test_accept_requires_captcha_when_enabled(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """开关开启：不带验证码 → 400 captcha_invalid，且订单未被改动。"""
    await _set_accept_captcha(db_session, True)
    order = await _create_order(client, admin_user)

    resp = await _accept(client, order["id"], booster_user)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "captcha_invalid"
    assert detail["message"] == "验证码错误或已过期"

    # 验证码闸门必须在 accept_order 之前：订单没有被锁定、没有名额
    detail_resp = await client.get(
        f"/orders/{order['id']}", headers=auth_header(admin_user)
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["status"] == "PENDING"
    assert detail_resp.json()["claimed_count"] == 0


async def test_accept_with_wrong_captcha_code(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """开关开启：验证码字符错误 → 400 captcha_invalid。"""
    await _set_accept_captcha(db_session, True)
    order = await _create_order(client, admin_user)
    payload = _make_accept_captcha()
    payload["captcha_code"] = "wrong"

    resp = await _accept(client, order["id"], booster_user, json=payload)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "captcha_invalid"


async def test_accept_with_correct_captcha_succeeds(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """开关开启：正确的 accept 验证码 → 接单成功。"""
    await _set_accept_captcha(db_session, True)
    order = await _create_order(client, admin_user)

    payload = _make_accept_captcha()
    resp = await _accept(client, order["id"], booster_user, json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["booster_id"] == booster_user["user"]["id"]
    assert data["status"] == "LOCKED"


async def test_register_captcha_cannot_be_used_for_accept(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
):
    """场景隔离：注册验证码不能用于接单。"""
    await _set_accept_captcha(db_session, True)
    order = await _create_order(client, admin_user)

    register_payload = make_captcha()  # scene="register"
    resp = await _accept(
        client, order["id"], booster_user, json=register_payload
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "captcha_invalid"

    # 再试一次（若跨场景被误放行，第一次就已经 200，这里兜底断言订单仍可接）
    detail_resp = await client.get(
        f"/orders/{order['id']}", headers=auth_header(admin_user)
    )
    assert detail_resp.json()["claimed_count"] == 0


async def test_get_accept_captcha_disabled_returns_400(
    client: AsyncClient, booster_user: dict, db_session
):
    """开关关闭：不发放接单验证码。"""
    await _set_accept_captcha(db_session, False)

    resp = await client.get(
        "/orders/accept-captcha", headers=auth_header(booster_user)
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "接单验证码已关闭"


async def test_get_accept_captcha_enabled_returns_image(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """开关开启：返回 captcha_id 与 base64 data URI 图片。"""
    await _set_accept_captcha(db_session, True)

    resp = await client.get(
        "/orders/accept-captcha", headers=auth_header(booster_user)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["captcha_id"]
    assert data["image"].startswith("data:image/png;base64,")

    # 生成的验证码是 accept 场景，可直接用于接单
    order = await _create_order(client, admin_user)
    code = captcha_service._store[data["captcha_id"]][0]
    accept_resp = await _accept(
        client,
        order["id"],
        booster_user,
        json={"captcha_id": data["captcha_id"], "captcha_code": code},
    )
    assert accept_resp.status_code == 200, accept_resp.text


async def test_order_detail_exposes_accept_captcha_required_flag(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """订单详情按服务端开关回填 accept_captcha_required（列表保持默认 False）。"""
    order = await _create_order(client, admin_user)

    await _set_accept_captcha(db_session, False)
    detail = await client.get(
        f"/orders/{order['id']}", headers=auth_header(booster_user)
    )
    assert detail.status_code == 200
    assert detail.json()["accept_captcha_required"] is False

    await _set_accept_captcha(db_session, True)
    detail = await client.get(
        f"/orders/{order['id']}", headers=auth_header(booster_user)
    )
    assert detail.status_code == 200
    assert detail.json()["accept_captcha_required"] is True
