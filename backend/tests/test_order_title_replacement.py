"""订单标题关键词替换：默认规则、后台自定义规则、编辑时再次替换。"""

from httpx import AsyncClient

from tests.conftest import auth_header


def _order_payload(title: str) -> dict:
    return {
        "game_name": "三角洲行动",
        "title": title,
        "price": "100.00",
        "boss_contact": "B0001",
    }


async def test_default_rules_replace_brand_words(
    raw_client: AsyncClient, admin_user: dict
):
    resp = await raw_client.post(
        "/orders/create",
        json=_order_payload("汀韵派单名牌pc绝密双护累计600w"),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["title"] == "汀韵派单【MP】pc绝密双护累计600w"


async def test_admin_can_configure_replacement_rules(
    client: AsyncClient, raw_client: AsyncClient, admin_user: dict
):
    resp = await client.put(
        "/admin/site/settings",
        json={
            "site_name": "测试站",
            "site_description": None,
            "order_title_replacements": [
                {"original": "绝密", "replacement": "【SECRET】"},
            ],
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["order_title_replacements"] == [
        {"original": "绝密", "replacement": "【SECRET】"}
    ]

    resp = await raw_client.post(
        "/orders/create",
        json=_order_payload("名牌双护绝密累计600w"),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201, resp.text
    # 自定义规则覆盖默认规则：名牌不再替换，绝密被替换
    assert resp.json()["title"] == "名牌双护【SECRET】累计600w"


async def test_update_order_reapplies_rules(
    raw_client: AsyncClient, admin_user: dict
):
    created = await raw_client.post(
        "/orders/create",
        json=_order_payload("正常标题"),
        headers=auth_header(admin_user),
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    updated = await raw_client.put(
        f"/orders/{order_id}",
        json={"title": "改单后明牌累计600w"},
        headers=auth_header(admin_user),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "改单后【MP】累计600w"
