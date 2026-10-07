"""后台「派单管理」列表的搜索 / 筛选 / 分页。

此前 /admin/orders 只透传 status 且前端只拉前 50 条、无翻页，
第 51 条起的订单后台看不到（老板 2026-10-07 反馈"派单管理还没有搜索"）。
本次补齐与用户端一致的 q / game_name / boss_contact / status 能力。
"""
import pytest
from httpx import AsyncClient

from tests.conftest import auth_header
from tests.test_escrow import _adjust_balance, _register


async def _publish(client: AsyncClient, publisher: dict, *, title: str, game: str, boss: str) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": game,
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "max_claims": 1,
            "title": title,
            "boss_contact": boss,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _admin_list(client: AsyncClient, admin_user: dict, **params) -> dict:
    resp = await client.get("/admin/orders", params=params, headers=auth_header(admin_user))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_admin_orders_search_by_order_id(
    client: AsyncClient, admin_user: dict, booster_user: dict, make_captcha
):
    """q 精确命中订单号（含 "#36" 形式），并按 id 升序命中唯一一条。"""
    publisher = await _register(client, "admin-search-pub@example.com", "AdminSearchPub")
    await _adjust_balance(client, admin_user, publisher, "1000.00")
    order = await _publish(
        client, publisher, title="搜索目标单", game="王者荣耀", boss="boss-alpha"
    )

    by_id = await _admin_list(client, admin_user, q=str(order["id"]))
    assert [item["id"] for item in by_id["items"]] == [order["id"]]

    by_hash = await _admin_list(client, admin_user, q=f"#{order['id']}")
    assert [item["id"] for item in by_hash["items"]] == [order["id"]]


async def test_admin_orders_search_by_game_and_title(
    client: AsyncClient, admin_user: dict
):
    """q / game_name 分别按标题关键词与游戏名过滤。"""
    publisher = await _register(client, "admin-search2-pub@example.com", "AdminSearch2Pub")
    await _adjust_balance(client, admin_user, publisher, "1000.00")
    honor = await _publish(
        client, publisher, title="王者上星指定打手", game="王者荣耀", boss="boss-beta"
    )
    lol = await _publish(
        client, publisher, title="英雄联盟定级赛", game="英雄联盟", boss="boss-gamma"
    )

    by_game = await _admin_list(client, admin_user, game_name="英雄联盟")
    assert honor["id"] not in [item["id"] for item in by_game["items"]]
    assert lol["id"] in [item["id"] for item in by_game["items"]]

    by_title = await _admin_list(client, admin_user, q="定级赛")
    assert [item["id"] for item in by_title["items"]] == [lol["id"]]


async def test_admin_orders_search_by_boss_contact(
    client: AsyncClient, admin_user: dict
):
    """boss_contact 模糊匹配：两条同前缀单都命中，别的单不命中。"""
    publisher = await _register(client, "admin-search3-pub@example.com", "AdminSearch3Pub")
    await _adjust_balance(client, admin_user, publisher, "1000.00")
    first = await _publish(
        client, publisher, title="老板A的单", game="王者荣耀", boss="boss-zeta-1"
    )
    second = await _publish(
        client, publisher, title="老板A第二单", game="王者荣耀", boss="boss-zeta-2"
    )
    other = await _publish(
        client, publisher, title="老板B的单", game="王者荣耀", boss="boss-omega"
    )

    data = await _admin_list(client, admin_user, boss_contact="boss-zeta")
    ids = {item["id"] for item in data["items"]}
    assert {first["id"], second["id"]} <= ids
    assert other["id"] not in ids


async def test_admin_orders_status_filter_and_pagination(
    client: AsyncClient, admin_user: dict
):
    """status 过滤 + 分页：page_size=1 时 pages/total 正确，第一页不带第二页的单。"""
    publisher = await _register(client, "admin-page-pub@example.com", "AdminPagePub")
    await _adjust_balance(client, admin_user, publisher, "1000.00")
    first = await _publish(client, publisher, title="分页单一", game="王者荣耀", boss="boss-page-1")
    second = await _publish(client, publisher, title="分页单二", game="王者荣耀", boss="boss-page-2")

    page1 = await _admin_list(client, admin_user, status="PENDING", page_size=1, page=1)
    page2 = await _admin_list(client, admin_user, status="PENDING", page_size=1, page=2)
    assert page1["page_size"] == 1
    assert len(page1["items"]) == 1
    # 两页各一条且不重复（同一秒发布时 created_at 相同，排序不稳定故不断言先后）
    page1_ids = {item["id"] for item in page1["items"]}
    page2_ids = {item["id"] for item in page2["items"]}
    assert page1_ids != page2_ids
    assert page1["pages"] == page2["pages"] >= 2
    assert page1["total"] == page2["total"] >= 2
    assert {first["id"], second["id"]} <= (page1_ids | page2_ids)


async def test_admin_orders_cancelling_filter(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session, make_captcha
):
    """status=CANCELLING 命中有取消协商挂起的订单，处理后消失。"""
    publisher = await _register(client, "admin-cancel-pub@example.com", "AdminCancelPub")
    await _adjust_balance(client, admin_user, publisher, "1000.00")
    plain = await _publish(client, publisher, title="无协商单", game="王者荣耀", boss="boss-plain")
    negotiating = await _publish(client, publisher, title="协商中的单", game="王者荣耀", boss="boss-neg")

    accepted = await client.put(
        f"/orders/{negotiating['id']}/accept", headers=auth_header(booster_user)
    )
    assert accepted.status_code == 200, accepted.text
    claims = await client.get(
        f"/orders/{negotiating['id']}/claims", headers=auth_header(publisher)
    )
    claim_id = next(
        item["id"]
        for item in claims.json()["items"]
        if item["booster_id"] == booster_user["user"]["id"]
    )
    created = await client.post(
        f"/orders/{negotiating['id']}/cancel-requests",
        json={"claim_id": claim_id, "reason": "后台筛选验证协商", "compensation_amount": 0},
        headers=auth_header(publisher),
    )
    assert created.status_code == 200, created.text

    await db_session.rollback()
    cancelling = await _admin_list(client, admin_user, status="CANCELLING")
    ids = [item["id"] for item in cancelling["items"]]
    assert negotiating["id"] in ids
    assert plain["id"] not in ids
    assert all(item["cancel_pending"] for item in cancelling["items"])

    # 拒绝后不再命中
    await db_session.rollback()
    rejected = await client.post(
        f"/orders/{negotiating['id']}/cancel-requests/{created.json()['id']}/decision",
        json={"action": "reject"},
        headers=auth_header(booster_user),
    )
    assert rejected.status_code == 200, rejected.text
    after = await _admin_list(client, admin_user, status="CANCELLING")
    assert negotiating["id"] not in [item["id"] for item in after["items"]]


async def test_admin_orders_rejects_unknown_status_and_requires_admin(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """非法状态值中文 422；非管理员 403。"""
    bad = await client.get(
        "/admin/orders", params={"status": "BOGUS"}, headers=auth_header(admin_user)
    )
    assert bad.status_code == 422, bad.text
    assert "状态筛选值无效" in bad.json()["detail"]

    forbidden = await client.get(
        "/admin/orders", headers=auth_header(booster_user)
    )
    assert forbidden.status_code == 403, forbidden.text
