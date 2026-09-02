"""Multi-slot (名额制) order lifecycle tests.

A max_claims=3 order must stay claimable in the hall until every slot is
settled; reviewing one booster's delivery only affects that booster's claim.
"""

from httpx import AsyncClient

from tests.conftest import auth_header


async def _register(client: AsyncClient, email: str, username: str) -> dict:
    resp = await client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": "Passw0rd123"},
    )
    assert resp.status_code in (200, 201)
    return resp.json()


async def _create_multi_slot_order(client: AsyncClient, admin_user: dict) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "200.00",
            "max_claims": 3,
        },
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 201
    return resp.json()


def _hall_ids(data: dict) -> set[int]:
    return {item["id"] for item in data["items"]}


async def test_multi_slot_order_stays_claimable_until_all_settled(
    client: AsyncClient, admin_user: dict
):
    """A 接单交付审核通过后：订单不 COMPLETED、大厅仍可见、B 仍可接单；
    三个名额全部结算后订单自动 COMPLETED。"""
    order = await _create_multi_slot_order(client, admin_user)
    order_id = order["id"]

    booster_a = await _register(client, "mca@example.com", "ClaimerA")
    booster_b = await _register(client, "mcb@example.com", "ClaimerB")
    booster_c = await _register(client, "mcc@example.com", "ClaimerC")

    # --- A claims and delivers ------------------------------------------
    resp = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster_a)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "LOCKED"

    resp = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "A 的交付说明"},
        headers=auth_header(booster_a),
    )
    assert resp.status_code == 200
    delivered = resp.json()
    assert delivered["status"] == "LOCKED"  # order keeps running
    assert delivered["my_claim"]["status"] == "DELIVERED"
    assert delivered["my_claim"]["delivery_note"] == "A 的交付说明"

    # --- Admin approves A's claim ----------------------------------------
    resp = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    claims = resp.json()["items"]
    assert len(claims) == 1
    claim_a = claims[0]
    assert claim_a["status"] == "DELIVERED"
    assert claim_a["is_first"] is True

    resp = await client.put(
        f"/orders/{order_id}/claims/{claim_a['id']}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    reviewed = resp.json()
    assert reviewed["status"] == "SETTLED"
    assert reviewed["settled_at"] is not None

    # The order is NOT completed and remains visible in the hall
    resp = await client.get(
        f"/orders/{order_id}", headers=auth_header(booster_b)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "LOCKED"

    resp = await client.get("/orders/", headers=auth_header(booster_b))
    assert resp.status_code == 200
    assert order_id in _hall_ids(resp.json())

    # --- B can still claim after A was settled ---------------------------
    resp = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster_b)
    )
    assert resp.status_code == 200

    # A's settlement did not leak into B's wallet
    resp = await client.get("/wallet", headers=auth_header(booster_b))
    assert resp.json()["available_balance"] == "0.00"

    # --- B delivers and is reviewed --------------------------------------
    resp = await client.put(
        f"/orders/{order_id}/deliver", headers=auth_header(booster_b)
    )
    assert resp.status_code == 200

    resp = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(admin_user)
    )
    claims = {c["booster_id"]: c for c in resp.json()["items"]}
    assert len(claims) == 2
    claim_b = claims[booster_b["user"]["id"]]
    assert claim_b["status"] == "DELIVERED"
    assert claims[booster_a["user"]["id"]]["status"] == "SETTLED"

    resp = await client.put(
        f"/orders/{order_id}/claims/{claim_b['id']}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200

    # Two of three slots settled: order still not completed, hall still open
    resp = await client.get(f"/orders/{order_id}", headers=auth_header(admin_user))
    assert resp.json()["status"] == "LOCKED"
    assert resp.json()["settled_count"] == 2

    resp = await client.get("/orders/", headers=auth_header(booster_c))
    assert order_id in _hall_ids(resp.json())

    # --- Third booster fills the last slot --------------------------------
    resp = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster_c)
    )
    assert resp.status_code == 200
    assert resp.json()["claimed_count"] == 3

    # Hall no longer lists the order for uninvolved users (quota exhausted).
    # 接单者本人仍能通过 my_claim 作用域在列表里看到自己接的单（订单Tab数据源）。
    outsider = await _register(client, "mcd@example.com", "OutsiderD")
    resp = await client.get("/orders/", headers=auth_header(outsider))
    assert order_id not in _hall_ids(resp.json())

    resp = await client.put(
        f"/orders/{order_id}/deliver", headers=auth_header(booster_c)
    )
    assert resp.status_code == 200

    resp = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(admin_user)
    )
    claim_c = {
        c["booster_id"]: c for c in resp.json()["items"]
    }[booster_c["user"]["id"]]

    resp = await client.put(
        f"/orders/{order_id}/claims/{claim_c['id']}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200

    # All claims settled + quota exhausted -> order auto-completes
    resp = await client.get(f"/orders/{order_id}", headers=auth_header(admin_user))
    data = resp.json()
    assert data["status"] == "COMPLETED"
    assert data["completed_at"] is not None
    assert data["settled_count"] == 3
    assert data["pending_review_count"] == 0

    # Every booster was credited independently (COMMISSION_RATE = 0)
    for booster in (booster_a, booster_b, booster_c):
        resp = await client.get("/wallet", headers=auth_header(booster))
        assert resp.json()["available_balance"] == "200.00"

    # claims/mine reflects each booster's own records
    resp = await client.get(
        "/orders/claims/mine?status=SETTLED", headers=auth_header(booster_a)
    )
    assert resp.status_code == 200
    mine = resp.json()
    assert mine["total"] == 1
    assert mine["items"][0]["order"]["id"] == order_id
    assert mine["items"][0]["order"]["status"] == "COMPLETED"
    assert mine["items"][0]["order"]["max_claims"] == 3


async def test_review_claim_validations(
    client: AsyncClient, admin_user: dict, booster_user: dict, registered_user: dict
):
    """Review endpoint: 403 non-admin, 400 wrong state, 400 amount over cap."""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "200.00",
            "price_max": "300.00",
            "max_claims": 2,
        },
        headers=auth_header(admin_user),
    )
    order = resp.json()

    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))
    await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster_user))

    resp = await client.get(
        f"/orders/{order['id']}/claims", headers=auth_header(admin_user)
    )
    claim = resp.json()["items"][0]

    # Non-admin cannot review
    resp = await client.put(
        f"/orders/{order['id']}/claims/{claim['id']}/review",
        json={"action": "approve"},
        headers=auth_header(booster_user),
    )
    assert resp.status_code == 403

    # Amount above max(price, price_max) is rejected
    resp = await client.put(
        f"/orders/{order['id']}/claims/{claim['id']}/review",
        json={"action": "approve", "amount": "300.01"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400

    # Approve within the range price_max cap works
    resp = await client.put(
        f"/orders/{order['id']}/claims/{claim['id']}/review",
        json={"action": "approve", "amount": "300.00"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "SETTLED"

    # Re-review of a settled claim is rejected
    resp = await client.put(
        f"/orders/{order['id']}/claims/{claim['id']}/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "该记录不在待审核状态"

    # Unknown claim id -> 404
    resp = await client.put(
        f"/orders/{order['id']}/claims/999999/review",
        json={"action": "approve"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 404


async def test_claims_mine_is_forbidden_for_admin(
    client: AsyncClient, admin_user: dict
):
    resp = await client.get("/orders/claims/mine", headers=auth_header(admin_user))
    assert resp.status_code == 403


async def test_order_claims_endpoint_is_admin_only(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """GET /orders/{id}/claims is admin-only now that claims/mine exists."""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "200.00",
        },
        headers=auth_header(admin_user),
    )
    order = resp.json()
    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster_user))

    resp = await client.get(
        f"/orders/{order['id']}/claims", headers=auth_header(booster_user)
    )
    assert resp.status_code == 403

    resp = await client.get(
        f"/orders/{order['id']}/claims", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_my_order_visibility_after_claiming(
    client: AsyncClient, admin_user: dict
):
    """LOCKED multi-claim orders stay in the booster's list after claiming."""
    order = await _create_multi_slot_order(client, admin_user)
    booster = await _register(client, "vis@example.com", "VisibilityBooster")

    resp = await client.get("/orders/", headers=auth_header(booster))
    assert order["id"] in _hall_ids(resp.json())

    await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster))

    # Still claimable in the hall (2 slots left) and carries my_claim
    resp = await client.get("/orders/", headers=auth_header(booster))
    data = resp.json()
    assert order["id"] in _hall_ids(data)
    mine = [item for item in data["items"] if item["id"] == order["id"]][0]
    assert mine["my_claim"] is not None
    assert mine["my_claim"]["status"] == "CLAIMED"

    # Fill the remaining slots so the order leaves the hall for outsiders...
    other_a = await _register(client, "vis2@example.com", "VisibilityBooster2")
    other_b = await _register(client, "vis3@example.com", "VisibilityBooster3")
    for user in (other_a, other_b):
        resp = await client.put(
            f"/orders/{order['id']}/accept", headers=auth_header(user)
        )
        assert resp.status_code == 200

    # 满员后：无关用户的大厅不再展示该单（接单者本人仍通过 my_claim 作用域可见）
    outsider = await _register(client, "vis4@example.com", "VisibilityOutsider")
    resp = await client.get("/orders/", headers=auth_header(outsider))
    assert order["id"] not in _hall_ids(resp.json())

    # ...but stays visible to the claimer via the my-claim scope
    resp = await client.get(
        f"/orders/{order['id']}", headers=auth_header(booster)
    )
    assert resp.status_code == 200
    assert resp.json()["my_claim"]["status"] == "CLAIMED"


async def test_confirm_with_multiple_delivered_claims_requires_individual_review(
    client: AsyncClient, admin_user: dict
):
    """confirm + amount with >1 DELIVERED claims -> 400 逐个审核."""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "200.00",
            "max_claims": 2,
        },
        headers=auth_header(admin_user),
    )
    order = resp.json()
    booster_a = await _register(client, "cfa@example.com", "ConfirmA")
    booster_b = await _register(client, "cfb@example.com", "ConfirmB")

    for booster in (booster_a, booster_b):
        await client.put(f"/orders/{order['id']}/accept", headers=auth_header(booster))
        await client.put(f"/orders/{order['id']}/deliver", headers=auth_header(booster))

    resp = await client.put(
        f"/orders/{order['id']}/confirm",
        json={"amount": "100.00"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "存在多个待审核记录，请逐个审核"

    # Plain confirm settles both claims at full price and completes the order
    resp = await client.put(
        f"/orders/{order['id']}/confirm",
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"

    for booster in (booster_a, booster_b):
        resp = await client.get("/wallet", headers=auth_header(booster))
        assert resp.json()["available_balance"] == "200.00"
