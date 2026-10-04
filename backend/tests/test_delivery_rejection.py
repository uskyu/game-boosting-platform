"""Delivery rejection returns a claim to the booster for corrected evidence."""

from io import BytesIO

from httpx import AsyncClient
from PIL import Image

from tests.conftest import auth_header


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(output, format="PNG")
    return output.getvalue()


async def _create_order(client: AsyncClient, admin_user: dict) -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
            "require_delivery_image": True,
        },
        headers=auth_header(admin_user),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _upload_proof(
    client: AsyncClient, booster_user: dict, order_id: int, filename: str
):
    return await client.post(
        f"/orders/{order_id}/deliver-attachments",
        files={"attachment": (filename, BytesIO(_png_bytes()), "image/png")},
        headers=auth_header(booster_user),
    )


async def test_reject_clears_submission_and_allows_booster_to_resubmit(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    order = await _create_order(client, admin_user)
    order_id = order["id"]

    accepted = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster_user)
    )
    assert accepted.status_code == 200, accepted.text

    uploaded = await _upload_proof(client, booster_user, order_id, "wrong.png")
    assert uploaded.status_code == 201, uploaded.text

    delivered = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "first submission"},
        headers=auth_header(booster_user),
    )
    assert delivered.status_code == 200, delivered.text
    claim_id = delivered.json()["my_claim"]["id"]
    assert delivered.json()["my_claim"]["status"] == "DELIVERED"

    # Evidence is immutable after submission until the reviewer makes a decision.
    locked_upload = await _upload_proof(client, booster_user, order_id, "late.png")
    assert locked_upload.status_code == 400

    rejected = await client.put(
        f"/orders/{order_id}/claims/{claim_id}/review",
        json={"action": "reject", "reason": "截图没有显示结算结果"},
        headers=auth_header(admin_user),
    )
    assert rejected.status_code == 200, rejected.text
    rejected_claim = rejected.json()
    assert rejected_claim["status"] == "CLAIMED"
    assert rejected_claim["delivery_note"] is None
    assert rejected_claim["delivery_attachments"] is None
    assert rejected_claim["delivered_at"] is None
    assert rejected_claim["delivery_rejection_reason"] == "截图没有显示结算结果"
    assert rejected_claim["delivery_rejected_at"] is not None

    # The booster sees the reason and can submit fresh proof on the same claim.
    detail = await client.get(f"/orders/{order_id}", headers=auth_header(booster_user))
    assert detail.status_code == 200
    assert detail.json()["my_claim"]["delivery_rejection_reason"] == "截图没有显示结算结果"
    replacement = await _upload_proof(client, booster_user, order_id, "correct.png")
    assert replacement.status_code == 201, replacement.text
    resubmitted = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "corrected submission"},
        headers=auth_header(booster_user),
    )
    assert resubmitted.status_code == 200, resubmitted.text
    assert resubmitted.json()["my_claim"]["status"] == "DELIVERED"
    assert resubmitted.json()["my_claim"]["delivery_rejection_reason"] is None
    assert len(resubmitted.json()["my_claim"]["delivery_attachments"]) == 1


async def test_rejection_requires_reason_and_only_owner_or_admin_can_reject(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    registered_user: dict,
):
    order = await _create_order(client, admin_user)
    order_id = order["id"]
    await client.put(f"/orders/{order_id}/accept", headers=auth_header(booster_user))
    await _upload_proof(client, booster_user, order_id, "proof.png")
    delivered = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "done"},
        headers=auth_header(booster_user),
    )
    claim_id = delivered.json()["my_claim"]["id"]

    missing_reason = await client.put(
        f"/orders/{order_id}/claims/{claim_id}/review",
        json={"action": "reject", "reason": "  "},
        headers=auth_header(admin_user),
    )
    assert missing_reason.status_code == 422

    forbidden = await client.put(
        f"/orders/{order_id}/claims/{claim_id}/review",
        json={"action": "reject", "reason": "图片内容错误"},
        headers=auth_header(registered_user),
    )
    assert forbidden.status_code == 403

