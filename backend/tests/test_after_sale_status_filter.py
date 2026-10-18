"""「售后中」(AFTER_SALE) 服务端筛选测试。

老板 2026-10-18：「接单和派单的状态筛选里加一个售后中，打手到时候不能提现了，
也好找哪一个订单被发起了售后」。

售后中不是订单真实状态，而是「存在进行中（PENDING）售后申请」这一正交标记，
因此走服务端伪值筛选（与 CANCELLING 同一手法），三处入口共用同一解析器：
- GET /orders/?mine_published=true&status=AFTER_SALE（我的派单）
- GET /orders/claims/mine?status=AFTER_SALE（我的接单，按 order_id + booster_id 匹配）
- GET /admin/orders?status=AFTER_SALE（后台派单管理）

本文件重点防两个回归：
1. 分页口径：筛选条件必须同时作用于 items 与 count_query，否则 total/pages 与
   实际条数不一致（本地过滤的老病就是这么来的）。
2. 接单侧口径：多名额订单只有真正被冻结提现的打手命中，其他打手不应看到「售后中」。
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import auth_header


async def _register(
    client: AsyncClient, make_captcha, email: str, username: str
) -> dict:
    response = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "TestPass123",
            **make_captcha(),
        },
    )
    assert response.status_code in (200, 201), response.text
    login = await client.post(
        "/auth/login", json={"email": email, "password": "TestPass123"}
    )
    assert login.status_code == 200, login.text
    return login.json()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: str) -> None:
    response = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "测试入账"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text


async def _create_order(
    client: AsyncClient, publisher: dict, *, price: str = "100.00", max_claims: int = 1
) -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
            "max_claims": max_claims,
        },
        headers=auth_header(publisher),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _accept(client: AsyncClient, booster: dict, order_id: int) -> dict:
    response = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _deliver(client: AsyncClient, booster: dict, order_id: int) -> dict:
    response = await client.put(
        f"/orders/{order_id}/deliver", headers=auth_header(booster)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _completed_order(
    client: AsyncClient,
    admin_user: dict,
    publisher: dict,
    booster: dict,
    price: str = "100.00",
) -> dict:
    """布置一单 COMPLETED：托管发布 → 接单 → 交付 → 老板确认。"""
    await _fund(client, admin_user, publisher, price)
    order = await _create_order(client, publisher, price=price)
    await _accept(client, booster, order["id"])
    await _deliver(client, booster, order["id"])
    confirmed = await client.put(
        f"/orders/{order['id']}/confirm", headers=auth_header(publisher)
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "COMPLETED"
    return order


async def _create_after_sale(
    client: AsyncClient, publisher: dict, order_id: int, amount: str = "30.00"
) -> dict:
    response = await client.post(
        f"/orders/{order_id}/after-sales",
        json={
            "reason": "打手未按约定时间完成，要求返还部分费用",
            "amount": amount,
            "images": [],
        },
        headers=auth_header(publisher),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _mine_published(
    client: AsyncClient, publisher: dict, status: str, **params
) -> dict:
    response = await client.get(
        "/orders/",
        params={"mine_published": "true", "status": status, **params},
        headers=auth_header(publisher),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _my_claims(client: AsyncClient, booster: dict, status: str, **params) -> dict:
    response = await client.get(
        "/orders/claims/mine",
        params={"status": status, **params},
        headers=auth_header(booster),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_mine_published_after_sale_filter_hits_completed_order(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """我的派单：AFTER_SALE 命中被发起售后的完成单，筛选与订单状态正交。"""
    publisher = await _register(
        client, make_captcha, "as-filter-pub@example.com", "AsFilterPub"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)

    # 无售后时不命中
    await db_session.rollback()
    assert (await _mine_published(client, publisher, "AFTER_SALE"))["total"] == 0

    await _create_after_sale(client, publisher, order["id"])

    await db_session.rollback()
    data = await _mine_published(client, publisher, "AFTER_SALE")
    assert data["total"] == 1
    assert data["pages"] == 1
    assert [item["id"] for item in data["items"]] == [order["id"]]
    # 筛选出来的是「已完成」订单，售后中是额外标记而不是订单状态
    assert data["items"][0]["status"] == "COMPLETED"
    assert data["items"][0]["after_sale_pending"] is True


async def test_my_claims_after_sale_filter_hits_own_claim(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """我的接单：AFTER_SALE 命中该打手自己名下、且订单有进行中售后的名额。"""
    publisher = await _register(
        client, make_captcha, "as-claim-pub@example.com", "AsClaimPub"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)

    await db_session.rollback()
    assert (await _my_claims(client, booster_user, "AFTER_SALE"))["total"] == 0

    await _create_after_sale(client, publisher, order["id"])

    await db_session.rollback()
    data = await _my_claims(client, booster_user, "AFTER_SALE")
    assert data["total"] == 1
    items = data["items"]
    assert items[0]["order"]["id"] == order["id"]
    # 名额自身生命周期不变，订单仍是已完成
    assert items[0]["status"] in ("SETTLED", "DELIVERED")
    assert items[0]["order"]["status"] == "COMPLETED"


async def test_after_sale_filter_drops_out_after_admin_reject(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """结案路径 1（管理员驳回）：两个筛选都不再命中，订单仍 COMPLETED。"""
    publisher = await _register(
        client, make_captcha, "as-reject-pub@example.com", "AsRejectPub"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"])

    await db_session.rollback()
    assert (await _mine_published(client, publisher, "AFTER_SALE"))["total"] == 1

    rejected = await client.post(
        f"/admin/after-sales/{created['id']}/review",
        json={"action": "reject", "reason": "凭证不足，予以驳回"},
        headers=auth_header(admin_user),
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"

    await db_session.rollback()
    assert (await _mine_published(client, publisher, "AFTER_SALE"))["total"] == 0
    await db_session.rollback()
    assert (await _my_claims(client, booster_user, "AFTER_SALE"))["total"] == 0

    detail = await client.get(
        f"/orders/{order['id']}", headers=auth_header(publisher)
    )
    assert detail.json()["status"] == "COMPLETED"
    assert detail.json()["after_sale_pending"] is False


async def test_after_sale_filter_drops_out_after_publisher_withdraw(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """结案路径 2（老板撤诉）：两个筛选都不再命中，订单仍 COMPLETED。"""
    publisher = await _register(
        client, make_captcha, "as-withdraw-pub@example.com", "AsWithdrawPub"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"])

    await db_session.rollback()
    assert (await _my_claims(client, booster_user, "AFTER_SALE"))["total"] == 1

    withdrawn = await client.post(
        f"/orders/{order['id']}/after-sales/{created['id']}/withdraw",
        headers=auth_header(publisher),
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "CLOSED"

    await db_session.rollback()
    assert (await _mine_published(client, publisher, "AFTER_SALE"))["total"] == 0
    await db_session.rollback()
    assert (await _my_claims(client, booster_user, "AFTER_SALE"))["total"] == 0

    detail = await client.get(
        f"/orders/{order['id']}", headers=auth_header(publisher)
    )
    assert detail.json()["status"] == "COMPLETED"


async def test_after_sale_filter_drops_out_after_booster_accept(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """结案路径 3（打手同意退款）：两个筛选都不再命中，订单仍 COMPLETED。"""
    publisher = await _register(
        client, make_captcha, "as-accept-pub@example.com", "AsAcceptPub"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"])

    await db_session.rollback()
    assert (await _mine_published(client, publisher, "AFTER_SALE"))["total"] == 1

    accepted = await client.post(
        f"/orders/{order['id']}/after-sales/{created['id']}/accept",
        headers=auth_header(booster_user),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "APPROVED"

    await db_session.rollback()
    assert (await _mine_published(client, publisher, "AFTER_SALE"))["total"] == 0
    await db_session.rollback()
    assert (await _my_claims(client, booster_user, "AFTER_SALE"))["total"] == 0

    detail = await client.get(
        f"/orders/{order['id']}", headers=auth_header(publisher)
    )
    assert detail.json()["status"] == "COMPLETED"


async def test_after_sale_filter_pagination_is_consistent(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """分页口径：page_size=1 翻两页，total/pages 只算命中项（专防 count_query 漏加条件）。

    造 2 笔带售后的完成单 + 1 笔无售后的完成单；若条件只加到分页查询没加到
    count_query，total 会变成 3、pages 变 3，这里会立刻失败。
    """
    publisher = await _register(
        client, make_captcha, "as-page-pub@example.com", "AsPagePub"
    )
    first = await _completed_order(client, admin_user, publisher, booster_user)
    second = await _completed_order(client, admin_user, publisher, booster_user)
    # 无售后的对照单只用于证明 total 不把它算进来
    await _completed_order(client, admin_user, publisher, booster_user)

    await _create_after_sale(client, publisher, first["id"])
    await _create_after_sale(client, publisher, second["id"])

    await db_session.rollback()
    page1 = await _mine_published(
        client, publisher, "AFTER_SALE", page=1, page_size=1
    )
    await db_session.rollback()
    page2 = await _mine_published(
        client, publisher, "AFTER_SALE", page=2, page_size=1
    )

    assert page1["total"] == 2
    assert page1["pages"] == 2
    assert page2["total"] == 2
    assert page2["pages"] == 2
    assert len(page1["items"]) == 1
    assert len(page2["items"]) == 1
    assert {page1["items"][0]["id"], page2["items"][0]["id"]} == {
        first["id"],
        second["id"],
    }

    # 我的接单同样翻页口径一致
    await db_session.rollback()
    claims_page1 = await _my_claims(
        client, booster_user, "AFTER_SALE", page=1, page_size=1
    )
    await db_session.rollback()
    claims_page2 = await _my_claims(
        client, booster_user, "AFTER_SALE", page=2, page_size=1
    )
    assert claims_page1["total"] == 2
    assert claims_page1["pages"] == 2
    assert len(claims_page1["items"]) == 1
    assert len(claims_page2["items"]) == 1
    assert {
        claims_page1["items"][0]["order"]["id"],
        claims_page2["items"][0]["order"]["id"],
    } == {first["id"], second["id"]}


async def test_after_sale_filter_ignores_order_without_after_sale(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """未发起售后的单（含未完成单）不会命中 AFTER_SALE。"""
    publisher = await _register(
        client, make_captcha, "as-plain-pub@example.com", "AsPlainPub"
    )
    completed = await _completed_order(client, admin_user, publisher, booster_user)

    # 一笔停在 PENDING 的单（无法发起售后）
    await _fund(client, admin_user, publisher, "100.00")
    pending = await _create_order(client, publisher)

    await db_session.rollback()
    data = await _mine_published(client, publisher, "AFTER_SALE")
    ids = [item["id"] for item in data["items"]]
    assert data["total"] == 0
    assert completed["id"] not in ids
    assert pending["id"] not in ids


async def test_multi_claim_after_sale_filter_only_hits_frozen_booster(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """多名额订单：只有该单 booster（真正被冻结提现的那个）命中「售后中」。

    售后记录写的是 order.booster_id；同一订单的其他打手既没被售后、也没被
    冻结提现，不应该在「我的接单 → 售后中」里看到这一单（与卡片上现有的
    「售后处理中」标签口径一致）。
    """
    publisher = await _register(
        client, make_captcha, "as-multi-pub@example.com", "AsMultiPub"
    )
    booster_a = await _register(
        client, make_captcha, "as-multi-a@example.com", "AsMultiA"
    )
    booster_b = await _register(
        client, make_captcha, "as-multi-b@example.com", "AsMultiB"
    )
    await _fund(client, admin_user, publisher, "200.00")
    order = await _create_order(client, publisher, max_claims=2)

    await _accept(client, booster_a, order["id"])
    await _deliver(client, booster_a, order["id"])
    await _accept(client, booster_b, order["id"])
    await _deliver(client, booster_b, order["id"])

    # 两个名额都审核通过 → 订单自动 COMPLETED（此时才允许发起售后）
    claims = await client.get(
        f"/orders/{order['id']}/claims", headers=auth_header(admin_user)
    )
    assert claims.status_code == 200, claims.text
    for claim in claims.json()["items"]:
        reviewed = await client.put(
            f"/orders/{order['id']}/claims/{claim['id']}/review",
            json={"action": "approve"},
            headers=auth_header(admin_user),
        )
        assert reviewed.status_code == 200, reviewed.text

    detail = await client.get(
        f"/orders/{order['id']}", headers=auth_header(admin_user)
    )
    assert detail.json()["status"] == "COMPLETED"
    # 首接打手持有 order.booster_id，售后记录的 booster_id 就是他
    assert detail.json()["booster_id"] == booster_a["user"]["id"]

    await _create_after_sale(client, publisher, order["id"])

    await db_session.rollback()
    mine_a = await _my_claims(client, booster_a, "AFTER_SALE")
    assert mine_a["total"] == 1
    assert mine_a["items"][0]["order"]["id"] == order["id"]

    await db_session.rollback()
    mine_b = await _my_claims(client, booster_b, "AFTER_SALE")
    assert mine_b["total"] == 0
    assert mine_b["items"] == []


async def test_admin_orders_after_sale_filter(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """后台 /admin/orders?status=AFTER_SALE 不 422 且能命中（防 R1 回归）。"""
    publisher = await _register(
        client, make_captcha, "as-admin-pub@example.com", "AsAdminPub"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await _create_after_sale(client, publisher, order["id"])

    await db_session.rollback()
    response = await client.get(
        "/admin/orders",
        params={"status": "AFTER_SALE"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == [order["id"]]
    assert data["items"][0]["status"] == "COMPLETED"
    assert data["items"][0]["after_sale_pending"] is True


async def test_after_sale_filter_rejects_invalid_status_value(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    """非法值仍然是中文 422（既有行为不回归）。"""
    publisher = await _register(
        client, make_captcha, "as-invalid-pub@example.com", "AsInvalidPub"
    )

    published = await client.get(
        "/orders/",
        params={"mine_published": "true", "status": "NOPE"},
        headers=auth_header(publisher),
    )
    assert published.status_code == 422, published.text
    assert "状态筛选值无效" in published.json()["detail"]

    claims = await client.get(
        "/orders/claims/mine",
        params={"status": "NOPE"},
        headers=auth_header(booster_user),
    )
    assert claims.status_code == 422, claims.text
    assert "状态筛选值无效" in claims.json()["detail"]

    admin = await client.get(
        "/admin/orders",
        params={"status": "AFTERSALE_PENDING"},
        headers=auth_header(admin_user),
    )
    assert admin.status_code == 422, admin.text
