"""售后申请后端测试：发起校验 / 防重 / 禁提现 / 后台审阅 / 订单标记。

已完成订单的布置走真实流程（发布托管 → 接单 → 交付 → 老板确认），与
test_orders / test_multi_claim 同一套路；只有「非已完成」用例才停在 PENDING。
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType
from app.models.order import Order, OrderStatus
from app.models.order_after_sale import AfterSaleStatus, OrderAfterSale
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
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


async def _fund_wallet(
    client: AsyncClient, admin_user: dict, user_id: int, amount: str
) -> None:
    """管理员调账入账（仿 test_withdrawals._fund_wallet）。"""
    response = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": amount, "reason": "测试入账"},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text


async def _create_order(
    client: AsyncClient, publisher: dict, price: str = "100.00"
) -> dict:
    response = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
        },
        headers=auth_header(publisher),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _completed_order(
    client: AsyncClient,
    admin_user: dict,
    publisher: dict,
    booster: dict,
    price: str = "100.00",
) -> dict:
    """布置一单 COMPLETED 订单：托管发布 → 接单 → 交付 → 老板确认。"""
    await _fund_wallet(client, admin_user, publisher["user"]["id"], "200.00")
    order = await _create_order(client, publisher, price)
    accepted = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(booster)
    )
    assert accepted.status_code == 200, accepted.text
    delivered = await client.put(
        f"/orders/{order['id']}/deliver", headers=auth_header(booster)
    )
    assert delivered.status_code == 200, delivered.text
    confirmed = await client.put(
        f"/orders/{order['id']}/confirm", headers=auth_header(publisher)
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "COMPLETED"
    return order


async def _create_after_sale(
    client: AsyncClient, publisher: dict, order_id: int, **overrides
):
    payload = {
        "reason": "打手未按约定时间完成，要求返还部分费用",
        "amount": "30.00",
        "images": [],
    }
    payload.update(overrides)
    return await client.post(
        f"/orders/{order_id}/after-sales",
        json=payload,
        headers=auth_header(publisher),
    )


async def _review(client: AsyncClient, admin_user: dict, after_sale_id: int, body: dict):
    return await client.post(
        f"/admin/after-sales/{after_sale_id}/review",
        json=body,
        headers=auth_header(admin_user),
    )


async def _wallet(db: AsyncSession, user_id: int) -> Wallet:
    """直读用户钱包行（请求已提交，rollback 掉测试侧快照后再查）。"""
    db.expire_all()
    result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    return result.scalar_one()


async def _after_sale_ledger(
    db: AsyncSession, *, order_id: int, user_id: int
) -> list[WalletTransaction]:
    """某用户钱包上与该订单相关的售后流水（扣款/收款）。"""
    result = await db.execute(
        select(WalletTransaction)
        .join(Wallet, WalletTransaction.wallet_id == Wallet.id)
        .where(
            WalletTransaction.order_id == order_id,
            Wallet.user_id == user_id,
            WalletTransaction.type.in_(
                (
                    WalletTransactionType.AFTERSALE_DEDUCTION,
                    WalletTransactionType.AFTERSALE_REFUND,
                )
            ),
        )
    )
    return list(result.scalars().all())


async def test_non_publisher_cannot_create_after_sale(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-pub@example.com", "AfterSalePub"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)

    # 无关用户
    stranger = await _register(
        client, make_captcha, "aftersale-stranger@example.com", "Stranger"
    )
    forbidden = await _create_after_sale(client, stranger, order["id"])
    assert forbidden.status_code == 403, forbidden.text
    assert "只有订单发布者" in forbidden.json()["detail"]

    # 该单打手同样不能以发布者身份发起
    as_booster = await _create_after_sale(client, booster_user, order["id"])
    assert as_booster.status_code == 403, as_booster.text


async def test_after_sale_requires_completed_order(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-pending-order@example.com", "PubPending"
    )
    await _fund_wallet(client, admin_user, publisher["user"]["id"], "200.00")
    order = await _create_order(client, publisher)

    rejected = await _create_after_sale(client, publisher, order["id"])
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["detail"] == "仅已完成的订单可以申请售后"


async def test_after_sale_amount_above_order_price_rejected(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-amount@example.com", "PubAmount"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)

    over = await _create_after_sale(client, publisher, order["id"], amount="100.01")
    assert over.status_code == 400, over.text
    assert over.json()["detail"] == "售后金额不能超过订单派单价"

    # 等于派单价允许
    exact = await _create_after_sale(client, publisher, order["id"], amount="100.00")
    assert exact.status_code == 201, exact.text


async def test_after_sale_reason_too_short(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-reason@example.com", "PubReason"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)

    short = await _create_after_sale(client, publisher, order["id"], reason="太短")
    assert short.status_code == 422, short.text


async def test_duplicate_pending_after_sale_conflicts(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-dup@example.com", "PubDup"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)

    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text

    duplicate = await _create_after_sale(client, publisher, order["id"])
    assert duplicate.status_code == 409, duplicate.text
    assert "待处理的售后申请" in duplicate.json()["detail"]


async def test_after_sale_images_must_belong_to_requester(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-images@example.com", "PubImages"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)

    foreign = await _create_after_sale(
        client,
        publisher,
        order["id"],
        images=["/uploads/aftersales/999/abc.png"],
    )
    assert foreign.status_code == 400, foreign.text
    assert foreign.json()["detail"] == "售后凭证图片路径无效"

    # 同一张重复提交只留一条
    own = "/uploads/aftersales/{}/proof.png".format(publisher["user"]["id"])
    deduped = await _create_after_sale(
        client, publisher, order["id"], images=[own, own]
    )
    assert deduped.status_code == 201, deduped.text
    assert deduped.json()["images"] == [own]


async def test_pending_after_sale_blocks_booster_withdrawal(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-withdraw@example.com", "PubWithdraw"
    )
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)

    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text

    # 打手入账后申请提现：售后 PENDING 期间被闸门拦下
    await _fund_wallet(client, admin_user, booster_id, "100.00")
    blocked = await client.post(
        "/withdrawals",
        json={
            "amount": "40.00",
            "channel": "ALIPAY",
            "account_name": "张三",
            "account_no": "13800000000",
        },
        headers=auth_header(booster_user),
    )
    assert blocked.status_code == 400, blocked.text
    assert "售后处理中" in blocked.json()["detail"]

    # 售后被驳回后闸门释放，可以正常提现
    rejected = await _review(
        client,
        admin_user,
        created.json()["id"],
        {"action": "reject", "reason": "证据不足，驳回售后"},
    )
    assert rejected.status_code == 200, rejected.text
    allowed = await client.post(
        "/withdrawals",
        json={
            "amount": "40.00",
            "channel": "ALIPAY",
            "account_name": "张三",
            "account_no": "13800000000",
        },
        headers=auth_header(booster_user),
    )
    assert allowed.status_code == 201, allowed.text


async def test_admin_review_reject_requires_reason(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-review@example.com", "PubReview"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text

    missing = await _review(client, admin_user, created.json()["id"], {"action": "reject"})
    assert missing.status_code == 400, missing.text
    assert "必须填写原因" in missing.json()["detail"]


async def test_pending_after_sale_flag_on_order_detail(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-flag@example.com", "PubFlag"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)

    before = await client.get(
        f"/orders/{order['id']}", headers=auth_header(publisher)
    )
    assert before.status_code == 200, before.text
    assert before.json()["after_sale_pending"] is False

    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text

    during = await client.get(
        f"/orders/{order['id']}", headers=auth_header(publisher)
    )
    assert during.json()["after_sale_pending"] is True

    # 结案后标记消失
    rejected = await _review(
        client,
        admin_user,
        created.json()["id"],
        {"action": "reject", "reason": "凭证不足，予以驳回"},
    )
    assert rejected.status_code == 200, rejected.text
    after = await client.get(
        f"/orders/{order['id']}", headers=auth_header(publisher)
    )
    assert after.json()["after_sale_pending"] is False


async def test_after_sale_lists_and_notifications(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-list@example.com", "PubList"
    )
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)

    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["status"] == "PENDING"
    assert Decimal(str(item["amount"])) == Decimal("30.00")
    assert item["order"]["id"] == order["id"]
    assert item["requester"]["username"] == "PubList"
    assert item["booster"]["username"] == "TestBooster"

    # 订单维度列表
    order_list = await client.get(
        f"/orders/{order['id']}/after-sales", headers=auth_header(publisher)
    )
    assert order_list.status_code == 200, order_list.text
    assert len(order_list.json()) == 1

    # 我的售后：双边都能看到
    as_requester = await client.get(
        "/after-sales/mine", headers=auth_header(publisher)
    )
    assert as_requester.status_code == 200, as_requester.text
    assert len(as_requester.json()) == 1
    as_booster = await client.get(
        "/after-sales/mine", params={"role": "booster"}, headers=auth_header(booster_user)
    )
    assert as_booster.status_code == 200, as_booster.text
    assert len(as_booster.json()) == 1

    # 无关用户看不到该单售后
    stranger = await _register(
        client, make_captcha, "aftersale-outsider@example.com", "Outsider"
    )
    forbidden = await client.get(
        f"/orders/{order['id']}/after-sales", headers=auth_header(stranger)
    )
    assert forbidden.status_code == 403, forbidden.text

    # 发起即通知打手与管理员
    db_session.expire_all()
    notifications = await db_session.execute(
        select(Notification).where(
            Notification.type == NotificationType.AFTERSALES_CREATED
        )
    )
    recipients = {row.user_id for row in notifications.scalars().all()}
    assert booster_id in recipients
    assert admin_user["user"]["id"] in recipients


async def test_admin_after_sale_list_and_approve(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    publisher = await _register(
        client, make_captcha, "aftersale-admin@example.com", "PubAdmin"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text

    listed = await client.get(
        "/admin/after-sales",
        params={"status": "PENDING", "order_id": order["id"], "q": "PubAdmin"},
        headers=auth_header(admin_user),
    )
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload["total"] == 1
    assert payload["items"][0]["username"] == "PubAdmin"
    assert payload["items"][0]["order"]["title"] is None  # 建单未填标题

    approved = await _review(
        client, admin_user, created.json()["id"], {"action": "approve"}
    )
    assert approved.status_code == 200, approved.text
    data = approved.json()
    assert data["status"] == "APPROVED"
    assert data["resolved_at"] is not None
    assert data["reviewed_by"] == admin_user["user"]["id"]

    # 已结案的申请不能重复审阅
    again = await _review(
        client, admin_user, created.json()["id"], {"action": "reject", "reason": "重复审阅"}
    )
    assert again.status_code == 409, again.text

    # 结案后打手禁提现闸门释放
    await _fund_wallet(client, admin_user, booster_user["user"]["id"], "100.00")
    withdrawal = await client.post(
        "/withdrawals",
        json={
            "amount": "40.00",
            "channel": "ALIPAY",
            "account_name": "张三",
            "account_no": "13800000000",
        },
        headers=auth_header(booster_user),
    )
    assert withdrawal.status_code == 201, withdrawal.text

    # 后台默认只看待处理；按订单号搜索也能命中
    empty = await client.get(
        "/admin/after-sales",
        params={"status": "PENDING"},
        headers=auth_header(admin_user),
    )
    assert empty.json()["total"] == 0
    by_order = await client.get(
        "/admin/after-sales",
        params={"order_id": order["id"]},
        headers=auth_header(admin_user),
    )
    assert by_order.json()["total"] == 1

    db_session.expire_all()
    stored = await db_session.execute(
        select(OrderAfterSale.status).where(OrderAfterSale.id == created.json()["id"])
    )
    assert stored.scalar_one() == AfterSaleStatus.APPROVED
    statuses = await db_session.execute(
        select(Order.status).where(Order.id == order["id"])
    )
    assert statuses.scalar_one() == OrderStatus.COMPLETED


async def test_approved_after_sale_transfers_funds_and_writes_bilateral_ledger(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """售后通过：打手可用余额 -amount、老板 +amount，双边流水 + 分视角通知。"""
    publisher = await _register(
        client, make_captcha, "aftersale-settle@example.com", "PubSettle"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    # 布置后（全局服务费 0、无炸单赔付）：打手入账 100，老板 200 托管 100 后
    # 托管被打款支出，可用余额剩 100。
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("100.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("100.00")

    created = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert created.status_code == 201, created.text
    approved = await _review(
        client, admin_user, created.json()["id"], {"action": "approve"}
    )
    assert approved.status_code == 200, approved.text

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.available_balance == Decimal("70.00")
    publisher_wallet = await _wallet(db_session, publisher_id)
    assert publisher_wallet.available_balance == Decimal("130.00")

    # 双边流水：类型/金额/余额快照
    deduction = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(deduction) == 1
    assert deduction[0].type == WalletTransactionType.AFTERSALE_DEDUCTION
    assert deduction[0].amount == Decimal("-30.00")
    assert deduction[0].balance_before == Decimal("100.00")
    assert deduction[0].balance_after == Decimal("70.00")
    assert deduction[0].order_id == order["id"]
    assert deduction[0].booster_id == booster_id

    refund = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=publisher_id
    )
    assert len(refund) == 1
    assert refund[0].type == WalletTransactionType.AFTERSALE_REFUND
    assert refund[0].amount == Decimal("30.00")
    assert refund[0].balance_before == Decimal("100.00")
    assert refund[0].balance_after == Decimal("130.00")

    # 分视角通知：打手侧告知扣划、老板侧告知收款
    db_session.expire_all()
    notifications = await db_session.execute(
        select(Notification).where(
            Notification.type == NotificationType.AFTERSALES_RESOLVED
        )
    )
    by_user = {row.user_id: row.content for row in notifications.scalars().all()}
    assert booster_id in by_user and publisher_id in by_user
    assert "已扣划 ¥30.00 给老板" in by_user[booster_id]
    assert "已收款 ¥30.00" in by_user[publisher_id]


async def test_approved_after_sale_deducts_to_zero_and_notes_shortfall(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """打手可用余额不足：扣至 0，差额写进 decision_reason，老板只收实扣。"""
    publisher = await _register(
        client, make_captcha, "aftersale-short@example.com", "PubShort"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    # 打手入账 100，管理员调账 -80 后可用余额只剩 20
    await _fund_wallet(client, admin_user, booster_id, "-80.00")
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("20.00")

    created = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert created.status_code == 201, created.text
    approved = await _review(
        client,
        admin_user,
        created.json()["id"],
        {"action": "approve", "reason": "同意售后赔付"},
    )
    assert approved.status_code == 200, approved.text

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.available_balance == Decimal("0.00")
    publisher_wallet = await _wallet(db_session, publisher_id)
    assert publisher_wallet.available_balance == Decimal("120.00")

    # 差额说明拼在管理员审阅意见之后
    db_session.expire_all()
    stored = await db_session.execute(
        select(OrderAfterSale.decision_reason).where(
            OrderAfterSale.id == created.json()["id"]
        )
    )
    assert stored.scalar_one() == (
        "同意售后赔付；打手可用余额不足 ¥10.00，按实际余额 ¥20.00 扣收"
    )

    # 流水按实扣金额记账：打手 -20（20 → 0），老板 +20（100 → 120）
    deduction = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(deduction) == 1
    assert deduction[0].amount == Decimal("-20.00")
    assert deduction[0].balance_before == Decimal("20.00")
    assert deduction[0].balance_after == Decimal("0.00")
    refund = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=publisher_id
    )
    assert len(refund) == 1
    assert refund[0].amount == Decimal("20.00")
    assert refund[0].balance_before == Decimal("100.00")
    assert refund[0].balance_after == Decimal("120.00")


async def test_rejected_after_sale_leaves_wallets_untouched(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """售后驳回：不动任何余额，不产生售后流水。"""
    publisher = await _register(
        client, make_captcha, "aftersale-reject-funds@example.com", "PubReject"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await _fund_wallet(client, admin_user, booster_id, "50.00")
    await db_session.rollback()
    booster_before = (await _wallet(db_session, booster_id)).available_balance
    publisher_before = (await _wallet(db_session, publisher_id)).available_balance
    assert booster_before == Decimal("150.00")

    created = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert created.status_code == 201, created.text
    rejected = await _review(
        client,
        admin_user,
        created.json()["id"],
        {"action": "reject", "reason": "凭证不足，予以驳回"},
    )
    assert rejected.status_code == 200, rejected.text

    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == booster_before
    assert (await _wallet(db_session, publisher_id)).available_balance == publisher_before
    assert (
        await _after_sale_ledger(db_session, order_id=order["id"], user_id=booster_id)
        == []
    )
    assert (
        await _after_sale_ledger(db_session, order_id=order["id"], user_id=publisher_id)
        == []
    )
