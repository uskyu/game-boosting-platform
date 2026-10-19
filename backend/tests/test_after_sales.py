"""售后申请后端测试：发起校验 / 防重 / 禁提现 / 后台审阅 / 打手秒结 / 订单标记。

已完成订单的布置走真实流程（发布托管 → 接单 → 交付 → 老板确认），与
test_orders / test_multi_claim 同一套路；只有「非已完成」用例才停在 PENDING。

扣款口径（老板 2026-10-18 拍板）：先扣可用余额 → 不足部分从保证金补扣 →
两处合计仍不足则 400 拒绝结案（售后保持 PENDING、双方余额零变动）。
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType
from app.models.order import Order, OrderStatus
from app.models.order_after_sale import AfterSaleStatus, OrderAfterSale
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from app.services import deposit_service
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


async def _deposit(
    client: AsyncClient, admin_user: dict, user_id: int, amount: str
) -> None:
    """管理员调保证金（独立余额池，不动可用余额）。"""
    response = await client.post(
        f"/admin/users/{user_id}/adjust-deposit",
        json={"delta": amount, "remark": "测试保证金"},
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


async def _withdraw(
    client: AsyncClient, user: dict, order_id: int, after_sale_id: int
):
    return await client.post(
        f"/orders/{order_id}/after-sales/{after_sale_id}/withdraw",
        headers=auth_header(user),
    )


async def _accept(
    client: AsyncClient, user: dict, order_id: int, after_sale_id: int
):
    return await client.post(
        f"/orders/{order_id}/after-sales/{after_sale_id}/accept",
        headers=auth_header(user),
    )


async def _wallet(db: AsyncSession, user_id: int) -> Wallet:
    """直读用户钱包行（请求已提交，rollback 掉测试侧快照后再查）。"""
    db.expire_all()
    result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    return result.scalar_one()


async def _after_sale_ledger(
    db: AsyncSession, *, order_id: int, user_id: int
) -> list[WalletTransaction]:
    """某用户钱包上与该订单相关的售后流水（可用余额扣款/保证金扣款/收款）。"""
    result = await db.execute(
        select(WalletTransaction)
        .join(Wallet, WalletTransaction.wallet_id == Wallet.id)
        .where(
            WalletTransaction.order_id == order_id,
            Wallet.user_id == user_id,
            WalletTransaction.type.in_(
                (
                    WalletTransactionType.AFTERSALE_DEDUCTION,
                    WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION,
                    WalletTransactionType.AFTERSALE_REFUND,
                )
            ),
        )
    )
    return list(result.scalars().all())


async def _wallet_ledger_by_type(
    db: AsyncSession, *, order_id: int, user_id: int
) -> dict[WalletTransactionType, list[WalletTransaction]]:
    """按流水类型分组，便于断言「可用余额几条、保证金几条」。"""
    grouped: dict[WalletTransactionType, list[WalletTransaction]] = {}
    for row in await _after_sale_ledger(db, order_id=order_id, user_id=user_id):
        grouped.setdefault(row.type, []).append(row)
    return grouped


async def _deposit_balance(db: AsyncSession, user_id: int) -> Decimal:
    """直读用户保证金余额。"""
    return (await _wallet(db, user_id)).deposit_balance


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
    db_session: AsyncSession,
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
    # 标题自 2026-10-19 起必填，conftest 的建单垫片统一注入「测试订单」；
    # 这里断言它确实透传进售后单的 order 快照（此前断言 None = 建单未填标题）。
    assert payload["items"][0]["order"]["title"] == "测试订单"

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


async def test_approved_after_sale_uses_deposit_to_cover_shortfall(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """管理员 approve 路径：可用余额不足时从保证金补扣，老板足额收款。

    老板 2026-10-18 截图场景的复现：申请 ¥30，打手可用 ¥20、保证金 ¥50。
    期望：可用 20 → 0、保证金 50 → 40、老板 +30；两条扣款流水（可用 + 保证金），
    各一条 AFTERSALE_DEDUCTION / AFTERSALE_DEPOSIT_DEDUCTION。
    """
    publisher = await _register(
        client, make_captcha, "aftersale-dep-approve@example.com", "PubDepApprove"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    # 打手入账 100，管理员调账 -80 后可用余额只剩 20；另缴 50 保证金
    await _fund_wallet(client, admin_user, booster_id, "-80.00")
    await _deposit(client, admin_user, booster_id, "50.00")
    await db_session.rollback()
    booster_before = await _wallet(db_session, booster_id)
    assert booster_before.available_balance == Decimal("20.00")
    assert booster_before.deposit_balance == Decimal("50.00")

    created = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert created.status_code == 201, created.text
    approved = await _review(
        client,
        admin_user,
        created.json()["id"],
        {"action": "approve", "reason": "同意售后赔付"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    # 扣款构成写进 decision_reason，后台/售后详情可核对
    assert approved.json()["decision_reason"] == (
        "同意售后赔付；可用余额扣 ¥20.00 + 保证金抵扣 ¥10.00"
    )

    # 打手：可用 20 → 0，保证金 50 → 40；老板足额 +30
    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.available_balance == Decimal("0.00")
    assert booster_wallet.deposit_balance == Decimal("40.00")
    publisher_wallet = await _wallet(db_session, publisher_id)
    assert publisher_wallet.available_balance == Decimal("130.00")

    # 流水分两条腿：可用余额 -20（20 → 0），保证金 -10（可用余额快照不变）
    grouped = await _wallet_ledger_by_type(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(grouped[WalletTransactionType.AFTERSALE_DEDUCTION]) == 1
    available_tx = grouped[WalletTransactionType.AFTERSALE_DEDUCTION][0]
    assert available_tx.amount == Decimal("-20.00")
    assert available_tx.balance_before == Decimal("20.00")
    assert available_tx.balance_after == Decimal("0.00")

    assert len(grouped[WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION]) == 1
    deposit_tx = grouped[WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION][0]
    assert deposit_tx.amount == Decimal("-10.00")
    # 保证金腿只动 deposit_balance，可用余额快照前后一致（钱包页不会歪）
    assert deposit_tx.balance_before == Decimal("0.00")
    assert deposit_tx.balance_after == Decimal("0.00")
    assert deposit_tx.order_id == order["id"]
    assert deposit_tx.booster_id == booster_id

    refund = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=publisher_id
    )
    assert len(refund) == 1
    assert refund[0].type == WalletTransactionType.AFTERSALE_REFUND
    assert refund[0].amount == Decimal("30.00")
    assert refund[0].balance_before == Decimal("100.00")
    assert refund[0].balance_after == Decimal("130.00")

    # 通知金额为实扣额（= 申请额），老板侧足额
    db_session.expire_all()
    notifications = await db_session.execute(
        select(Notification).where(
            Notification.type == NotificationType.AFTERSALES_RESOLVED
        )
    )
    by_user = {row.user_id: row.content for row in notifications.scalars().all()}
    assert "已扣划 ¥30.00 给老板" in by_user[booster_id]
    # 管理员路径把 decision_reason 后缀带进通知，抵扣构成随之可见
    assert "可用余额扣 ¥20.00 + 保证金抵扣 ¥10.00" in by_user[booster_id]
    assert "已收款 ¥30.00" in by_user[publisher_id]


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


async def test_approved_after_sale_with_adjusted_amount_settles_partial(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """approve 带裁定金额（20 < 申请 30）：只按 20 划转，裁定说明写进 decision_reason。"""
    publisher = await _register(
        client, make_captcha, "aftersale-adjust@example.com", "PubAdjust"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("100.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("100.00")

    created = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert created.status_code == 201, created.text
    approved = await _review(
        client,
        admin_user,
        created.json()["id"],
        {"action": "approve", "reason": "协商后部分赔付", "amount": "20.00"},
    )
    assert approved.status_code == 200, approved.text
    data = approved.json()
    assert data["status"] == "APPROVED"
    # 裁定说明拼在管理员审阅意见之后
    assert data["decision_reason"] == "协商后部分赔付；裁定扣款 ¥20.00（申请 ¥30.00）"

    # 只按裁定额 20 划转：打手 100 → 80，老板 100 → 120
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("80.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("120.00")

    # 流水按 20 记
    deduction = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(deduction) == 1
    assert deduction[0].amount == Decimal("-20.00")
    assert deduction[0].balance_before == Decimal("100.00")
    assert deduction[0].balance_after == Decimal("80.00")
    refund = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=publisher_id
    )
    assert len(refund) == 1
    assert refund[0].amount == Decimal("20.00")
    assert refund[0].balance_before == Decimal("100.00")
    assert refund[0].balance_after == Decimal("120.00")

    # 通知按裁定额 20 播报
    db_session.expire_all()
    notifications = await db_session.execute(
        select(Notification).where(
            Notification.type == NotificationType.AFTERSALES_RESOLVED
        )
    )
    by_user = {row.user_id: row.content for row in notifications.scalars().all()}
    assert "已扣划 ¥20.00 给老板" in by_user[booster_id]
    assert "已收款 ¥20.00" in by_user[publisher_id]


async def test_approved_after_sale_amount_above_claimed_rejected(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """approve 裁定金额超过申请金额：400，且不落状态、不动资金。"""
    publisher = await _register(
        client, make_captcha, "aftersale-over@example.com", "PubOver"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await db_session.rollback()
    booster_before = (await _wallet(db_session, booster_id)).available_balance
    publisher_before = (await _wallet(db_session, publisher_id)).available_balance

    created = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert created.status_code == 201, created.text
    over = await _review(
        client,
        admin_user,
        created.json()["id"],
        {"action": "approve", "amount": "40.00"},
    )
    assert over.status_code == 400, over.text
    assert over.json()["detail"] == "裁定扣款金额不能超过售后申请金额"

    # 状态仍是 PENDING，余额与流水均未动。
    # 先 rollback 结束旧快照：售后单是 API 侧事务提交的，REPEATABLE READ 下
    # 不换事务直接查会看不到（内存视图还停在建单之前）。
    await db_session.rollback()
    db_session.expire_all()
    stored = await db_session.execute(
        select(OrderAfterSale.status).where(OrderAfterSale.id == created.json()["id"])
    )
    assert stored.scalar_one() == AfterSaleStatus.PENDING
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == booster_before
    assert (await _wallet(db_session, publisher_id)).available_balance == publisher_before
    assert (
        await _after_sale_ledger(db_session, order_id=order["id"], user_id=booster_id)
        == []
    )


async def test_closed_after_sale_leaves_wallets_untouched(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """close：落 CLOSED、不动资金、无售后流水，双边通知「已关闭、平台未做裁定」。"""
    publisher = await _register(
        client, make_captcha, "aftersale-close@example.com", "PubClose"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await db_session.rollback()
    booster_before = (await _wallet(db_session, booster_id)).available_balance
    publisher_before = (await _wallet(db_session, publisher_id)).available_balance

    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text
    closed = await _review(
        client,
        admin_user,
        created.json()["id"],
        {"action": "close", "reason": "双方已线下协商一致"},
    )
    assert closed.status_code == 200, closed.text
    data = closed.json()
    assert data["status"] == "CLOSED"
    assert data["decision_reason"] == "双方已线下协商一致"
    assert data["reviewed_by"] == admin_user["user"]["id"]
    assert data["resolved_at"] is not None

    # 余额不动、无售后流水
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

    # 结案后打手禁提现闸门释放
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

    # 双边「已关闭、平台未做裁定」口径
    db_session.expire_all()
    notifications = await db_session.execute(
        select(Notification).where(
            Notification.type == NotificationType.AFTERSALES_RESOLVED
        )
    )
    by_user = {
        row.user_id: (row.title, row.content) for row in notifications.scalars().all()
    }
    assert booster_id in by_user and publisher_id in by_user
    for user_id in (booster_id, publisher_id):
        title, content = by_user[user_id]
        assert title == "售后申请已关闭"
        assert "售后申请已关闭，平台未做裁定" in content
        assert "双方已线下协商一致" in content


async def test_close_after_sale_requires_reason(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    """close 缺原因：400，售后保持 PENDING。"""
    publisher = await _register(
        client, make_captcha, "aftersale-close-noreason@example.com", "PubCloseNR"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text

    missing = await _review(
        client, admin_user, created.json()["id"], {"action": "close"}
    )
    assert missing.status_code == 400, missing.text
    assert "必须填写原因" in missing.json()["detail"]

    # 售后仍未处理（经「我的售后」回显核对）
    as_requester = await client.get(
        "/after-sales/mine", headers=auth_header(publisher)
    )
    assert as_requester.status_code == 200, as_requester.text
    assert as_requester.json()[0]["status"] == "PENDING"


async def test_publisher_withdraws_pending_after_sale(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """老板撤诉：CLOSED + 发布人主动撤回，不动资金，双边撤回通知。"""
    publisher = await _register(
        client, make_captcha, "aftersale-withdraw-pub@example.com", "PubWithdrawAS"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await db_session.rollback()
    booster_before = (await _wallet(db_session, booster_id)).available_balance
    publisher_before = (await _wallet(db_session, publisher_id)).available_balance

    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text
    withdrawn = await _withdraw(
        client, publisher, order["id"], created.json()["id"]
    )
    assert withdrawn.status_code == 200, withdrawn.text
    data = withdrawn.json()
    assert data["status"] == "CLOSED"
    assert data["decision_reason"] == "发布人主动撤回"
    assert data["resolved_at"] is not None

    # 余额不动、无售后流水；打手禁提现闸门释放
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == booster_before
    assert (await _wallet(db_session, publisher_id)).available_balance == publisher_before
    assert (
        await _after_sale_ledger(db_session, order_id=order["id"], user_id=booster_id)
        == []
    )
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

    # 双边撤回通知：打手侧说明提现限制已解除
    db_session.expire_all()
    notifications = await db_session.execute(
        select(Notification).where(
            Notification.type == NotificationType.AFTERSALES_RESOLVED
        )
    )
    by_user = {
        row.user_id: (row.title, row.content) for row in notifications.scalars().all()
    }
    assert booster_id in by_user and publisher_id in by_user
    for user_id in (booster_id, publisher_id):
        assert by_user[user_id][0] == "售后申请已撤回"
    assert "提现限制已解除" in by_user[booster_id][1]
    assert "不再处理" in by_user[publisher_id][1]


async def test_booster_cannot_withdraw_after_sale(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    """打手不是发布者，不能撤诉：403，售后保持 PENDING。"""
    publisher = await _register(
        client, make_captcha, "aftersale-withdraw-booster@example.com", "PubWithdrawBooster"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text

    forbidden = await _withdraw(
        client, booster_user, order["id"], created.json()["id"]
    )
    assert forbidden.status_code == 403, forbidden.text
    assert "只有售后申请发布者" in forbidden.json()["detail"]

    as_booster = await client.get(
        "/after-sales/mine", params={"role": "booster"}, headers=auth_header(booster_user)
    )
    assert as_booster.status_code == 200, as_booster.text
    assert as_booster.json()[0]["status"] == "PENDING"


async def test_withdraw_processed_after_sale_conflicts(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    """已 APPROVED 的售后不能再撤诉：409。"""
    publisher = await _register(
        client, make_captcha, "aftersale-withdraw-done@example.com", "PubWithdrawDone"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text

    approved = await _review(
        client, admin_user, created.json()["id"], {"action": "approve"}
    )
    assert approved.status_code == 200, approved.text

    conflict = await _withdraw(
        client, publisher, order["id"], created.json()["id"]
    )
    assert conflict.status_code == 409, conflict.text
    assert "无法撤回" in conflict.json()["detail"]


async def test_create_after_sale_blocked_when_order_already_approved(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """一单一裁：该单已有 APPROVED 售后时不能再次发起（重复扣款防护）。"""
    publisher = await _register(
        client, make_captcha, "aftersale-onepass@example.com", "PubOnePass"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert created.status_code == 201, created.text
    assert (await _review(client, admin_user, created.json()["id"], {"action": "approve"})).status_code == 200

    again = await _create_after_sale(client, publisher, order["id"], amount="10.00")
    assert again.status_code == 400, again.text
    assert again.json()["detail"] == "该订单已有已裁定通过的售后申请，不能再次发起"


async def test_approve_blocked_when_another_approved_exists(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """并发双裁兜底：同订单存在另一笔 APPROVED 时 approve 被 400 拦下。

    create 侧已拦多数场景，这里直接插第二笔 PENDING 绕过发起校验，
    专测 review 分支的权威兜底（唯一键 uq_wallet_tx_order_booster_type 之前）。
    """
    from decimal import Decimal as _Decimal

    publisher = await _register(
        client, make_captcha, "aftersale-dualp@example.com", "PubDual"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    first = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert first.status_code == 201, first.text
    assert (await _review(client, admin_user, first.json()["id"], {"action": "approve"})).status_code == 200

    # 直接插第二笔 PENDING（绕过 create 的「已有 APPROVED」拦截）
    second = OrderAfterSale(
        order_id=order["id"],
        requester_id=publisher["user"]["id"],
        booster_id=booster_user["user"]["id"],
        reason="第二笔售后（绕过发起校验直插）",
        images=[],
        amount=_Decimal("20.00"),
        status=AfterSaleStatus.PENDING,
    )
    db_session.add(second)
    await db_session.commit()
    await db_session.refresh(second)
    # id 先取标量：API 调用后实例属性过期，异步会话里再访问会触发懒加载
    # （MissingGreenlet），后续查询一律用这个 int。
    second_id = second.id

    resp = await _review(client, admin_user, second_id, {"action": "approve"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "该订单已有已通过的售后申请，不能重复裁定"

    await db_session.rollback()
    stored = await db_session.execute(
        select(OrderAfterSale.status).where(OrderAfterSale.id == second_id)
    )
    assert stored.scalar_one() == AfterSaleStatus.PENDING


async def test_booster_accepts_after_sale_and_settles_instantly(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """打手同意退款：APPROVED + decision_reason=打手同意退款、reviewed_by 留空，
    按申请金额全款秒结：打手 -amount、老板 +amount，双边流水 + 分视角通知。"""
    publisher = await _register(
        client, make_captcha, "aftersale-accept@example.com", "PubAccept"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("100.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("100.00")

    created = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert created.status_code == 201, created.text
    accepted = await _accept(client, booster_user, order["id"], created.json()["id"])
    assert accepted.status_code == 200, accepted.text
    data = accepted.json()
    assert data["status"] == "APPROVED"
    assert data["decision_reason"] == "打手同意退款"
    assert data["reviewed_by"] is None  # 无管理员参与，后台靠 decision_reason 识别
    assert data["resolved_at"] is not None

    # 按申请金额 30 全款秒结：打手 100 → 70，老板 100 → 130
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("70.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("130.00")

    # 双边流水：打手 -30（100 → 70），老板 +30（100 → 130）
    deduction = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(deduction) == 1
    assert deduction[0].type == WalletTransactionType.AFTERSALE_DEDUCTION
    assert deduction[0].amount == Decimal("-30.00")
    assert deduction[0].balance_before == Decimal("100.00")
    assert deduction[0].balance_after == Decimal("70.00")
    refund = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=publisher_id
    )
    assert len(refund) == 1
    assert refund[0].type == WalletTransactionType.AFTERSALE_REFUND
    assert refund[0].amount == Decimal("30.00")
    assert refund[0].balance_before == Decimal("100.00")
    assert refund[0].balance_after == Decimal("130.00")

    # 分视角通知：打手侧「你已同意退款…扣划给老板」，老板侧「打手同意退款…已收款」
    db_session.expire_all()
    notifications = await db_session.execute(
        select(Notification).where(
            Notification.type == NotificationType.AFTERSALES_RESOLVED
        )
    )
    by_user = {row.user_id: row.content for row in notifications.scalars().all()}
    assert booster_id in by_user and publisher_id in by_user
    assert "你已同意退款" in by_user[booster_id]
    assert "已从你的余额扣划给老板" in by_user[booster_id]
    assert "打手同意退款" in by_user[publisher_id]
    assert "已收款 ¥30.00" in by_user[publisher_id]


async def test_publisher_cannot_accept_after_sale(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    """老板不是接单打手，不能同意退款：403，售后保持 PENDING。"""
    publisher = await _register(
        client, make_captcha, "aftersale-accept-pub@example.com", "PubAcceptPub"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text

    forbidden = await _accept(client, publisher, order["id"], created.json()["id"])
    assert forbidden.status_code == 403, forbidden.text
    assert "只有该订单的打手可以执行此操作" in forbidden.json()["detail"]

    as_booster = await client.get(
        "/after-sales/mine", params={"role": "booster"}, headers=auth_header(booster_user)
    )
    assert as_booster.status_code == 200, as_booster.text
    assert as_booster.json()[0]["status"] == "PENDING"


async def test_accept_processed_after_sale_conflicts(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    make_captcha,
):
    """已驳回（非 PENDING）的售后不能再同意退款：409。"""
    publisher = await _register(
        client, make_captcha, "aftersale-accept-done@example.com", "PubAcceptDone"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"])
    assert created.status_code == 201, created.text

    rejected = await _review(
        client,
        admin_user,
        created.json()["id"],
        {"action": "reject", "reason": "证据不足，驳回售后"},
    )
    assert rejected.status_code == 200, rejected.text

    conflict = await _accept(client, booster_user, order["id"], created.json()["id"])
    assert conflict.status_code == 409, conflict.text
    assert "该售后申请已处理" in conflict.json()["detail"]


async def test_accept_blocked_when_another_approved_exists(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """一单一裁兜底：同订单存在另一笔 APPROVED 时打手同意退款被 400 拦下。

    create 侧已拦多数场景，这里直接插第二笔 PENDING 绕过发起校验，
    专测秒结路径的权威兜底（仿 test_approve_blocked_when_another_approved_exists，
    id 先取标量防 MissingGreenlet）。
    """
    publisher = await _register(
        client, make_captcha, "aftersale-accept-dualp@example.com", "PubAcceptDual"
    )
    order = await _completed_order(client, admin_user, publisher, booster_user)
    first = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert first.status_code == 201, first.text
    assert (
        await _review(client, admin_user, first.json()["id"], {"action": "approve"})
    ).status_code == 200

    # 直接插第二笔 PENDING（绕过 create 的「已有 APPROVED」拦截）
    second = OrderAfterSale(
        order_id=order["id"],
        requester_id=publisher["user"]["id"],
        booster_id=booster_user["user"]["id"],
        reason="第二笔售后（绕过发起校验直插）",
        images=[],
        amount=Decimal("20.00"),
        status=AfterSaleStatus.PENDING,
    )
    db_session.add(second)
    await db_session.commit()
    await db_session.refresh(second)
    # id 先取标量：API 调用后实例属性过期，异步会话里再访问会触发懒加载
    # （MissingGreenlet），后续查询一律用这个 int。
    second_id = second.id

    resp = await _accept(client, booster_user, order["id"], second_id)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "该订单已有已通过的售后申请，不能重复裁定"

    await db_session.rollback()
    stored = await db_session.execute(
        select(OrderAfterSale.status).where(OrderAfterSale.id == second_id)
    )
    assert stored.scalar_one() == AfterSaleStatus.PENDING


async def test_booster_accept_uses_deposit_to_cover_shortfall(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """打手同意退款：可用余额不足时从保证金补扣，老板足额收款（老板截图场景）。

    申请 ¥20、可用 ¥4.60、保证金 ¥119 —— 期望可用 0、保证金 103.60、老板 +20。
    """
    publisher = await _register(
        client, make_captcha, "aftersale-accept-dep@example.com", "PubAcceptDep"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    # 打手入账 100，调账 -95.40 后可用只剩 4.60；另缴 119 保证金
    await _fund_wallet(client, admin_user, booster_id, "-95.40")
    await _deposit(client, admin_user, booster_id, "119.00")
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("4.60")
    assert (await _deposit_balance(db_session, booster_id)) == Decimal("119.00")

    created = await _create_after_sale(client, publisher, order["id"], amount="20.00")
    assert created.status_code == 201, created.text
    accepted = await _accept(client, booster_user, order["id"], created.json()["id"])
    assert accepted.status_code == 200, accepted.text
    data = accepted.json()
    assert data["status"] == "APPROVED"
    assert data["reviewed_by"] is None
    assert data["decision_reason"] == "打手同意退款；可用余额扣 ¥4.60 + 保证金抵扣 ¥15.40"

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.available_balance == Decimal("0.00")
    assert booster_wallet.deposit_balance == Decimal("103.60")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("120.00")

    grouped = await _wallet_ledger_by_type(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(grouped[WalletTransactionType.AFTERSALE_DEDUCTION]) == 1
    assert grouped[WalletTransactionType.AFTERSALE_DEDUCTION][0].amount == Decimal("-4.60")
    assert len(grouped[WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION]) == 1
    deposit_tx = grouped[WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION][0]
    assert deposit_tx.amount == Decimal("-15.40")
    assert deposit_tx.balance_before == Decimal("0.00")
    assert deposit_tx.balance_after == Decimal("0.00")

    # 通知金额 == 实扣额（20，不再是截断后的 4.60），并说明扣款构成
    db_session.expire_all()
    notifications = await db_session.execute(
        select(Notification).where(
            Notification.type == NotificationType.AFTERSALES_RESOLVED
        )
    )
    by_user = {row.user_id: row.content for row in notifications.scalars().all()}
    assert "你已同意退款" in by_user[booster_id]
    assert "¥20.00 已从你的账户扣划给老板" in by_user[booster_id]
    assert "可用余额 ¥4.60 + 保证金 ¥15.40" in by_user[booster_id]
    assert "已收款 ¥20.00" in by_user[publisher_id]


async def test_deposit_offset_prefers_available_balance_first(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """扣款顺序回归：先扣可用余额，不足的部分才从保证金补（与取消赔偿同口径）。"""
    publisher = await _register(
        client, make_captcha, "aftersale-order@example.com", "PubOrderSeq"
    )
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    # 打手入账 100，调账 -90 后可用 10；保证金 100
    await _fund_wallet(client, admin_user, booster_id, "-90.00")
    await _deposit(client, admin_user, booster_id, "100.00")
    await db_session.rollback()

    created = await _create_after_sale(client, publisher, order["id"], amount="20.00")
    assert created.status_code == 201, created.text
    accepted = await _accept(client, booster_user, order["id"], created.json()["id"])
    assert accepted.status_code == 200, accepted.text

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    # 可用 10 全扣（→0），保证金只需补 10（100 → 90）；不是反过来先扣保证金
    assert booster_wallet.available_balance == Decimal("0.00")
    assert booster_wallet.deposit_balance == Decimal("90.00")

    grouped = await _wallet_ledger_by_type(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert grouped[WalletTransactionType.AFTERSALE_DEDUCTION][0].amount == Decimal("-10.00")
    assert (
        grouped[WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION][0].amount
        == Decimal("-10.00")
    )


async def test_accept_rejected_when_total_funds_insufficient(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """核心用例：可用 + 保证金仍不足 → 400，售后仍 PENDING，双方余额零变动。

    这是老板拍板的硬要求（禁止静默少收结案）：报错让打手知道还缺多少，
    而不是扣到哪算哪。断言资金、流水、状态三处都没动。
    """
    publisher = await _register(
        client, make_captcha, "aftersale-insufficient@example.com", "PubInsufficient"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    # 打手入账 100，调账 -99 后可用 1；保证金 2 → 合计 3 < 申请 20
    await _fund_wallet(client, admin_user, booster_id, "-99.00")
    await _deposit(client, admin_user, booster_id, "2.00")
    await db_session.rollback()
    booster_before = await _wallet(db_session, booster_id)
    assert booster_before.available_balance == Decimal("1.00")
    assert booster_before.deposit_balance == Decimal("2.00")
    publisher_before = (await _wallet(db_session, publisher_id)).available_balance

    created = await _create_after_sale(client, publisher, order["id"], amount="20.00")
    assert created.status_code == 201, created.text
    after_sale_id = created.json()["id"]

    rejected = await _accept(client, booster_user, order["id"], after_sale_id)
    assert rejected.status_code == 400, rejected.text
    detail = rejected.json()["detail"]
    # 结构化 error：前端可据此给出「缺多少、怎么办」的可操作提示
    assert detail["code"] == "after_sale_insufficient_funds"
    assert "尚缺 ¥17.00" in detail["message"]
    assert "请先充值或缴纳保证金后再同意退款" in detail["message"]
    assert detail["required"] == "20.00"
    assert detail["available"] == "1.00"
    assert detail["deposit"] == "2.00"
    assert detail["shortfall"] == "17.00"

    # 状态不变：售后仍 PENDING（不会被静默结案）
    await db_session.rollback()
    stored = await db_session.execute(
        select(OrderAfterSale.status, OrderAfterSale.resolved_at).where(
            OrderAfterSale.id == after_sale_id
        )
    )
    status_value, resolved_at = stored.one()
    assert status_value == AfterSaleStatus.PENDING
    assert resolved_at is None

    # 资金零变动：双方可用余额、打手保证金都没动
    await db_session.rollback()
    booster_after = await _wallet(db_session, booster_id)
    assert booster_after.available_balance == Decimal("1.00")
    assert booster_after.deposit_balance == Decimal("2.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == publisher_before

    # 零售后流水（双方都没有）
    assert (
        await _after_sale_ledger(db_session, order_id=order["id"], user_id=booster_id)
        == []
    )
    assert (
        await _after_sale_ledger(db_session, order_id=order["id"], user_id=publisher_id)
        == []
    )

    # 打手仍被禁提现（售后还挂着）
    blocked = await client.post(
        "/withdrawals",
        json={
            "amount": "1.00",
            "channel": "ALIPAY",
            "account_name": "张三",
            "account_no": "13800000000",
        },
        headers=auth_header(booster_user),
    )
    assert blocked.status_code == 400, blocked.text
    assert "售后处理中" in blocked.json()["detail"]


async def test_admin_approve_rejected_when_total_funds_insufficient(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """管理员 approve 同样不允许静默少收：可扣款不足 → 400、不结案、零变动。"""
    publisher = await _register(
        client, make_captcha, "aftersale-admin-insuf@example.com", "PubAdminInsuf"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await _fund_wallet(client, admin_user, booster_id, "-100.00")  # 可用 0
    await _deposit(client, admin_user, booster_id, "10.00")
    await db_session.rollback()

    created = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert created.status_code == 201, created.text
    rejected = await _review(
        client, admin_user, created.json()["id"], {"action": "approve"}
    )
    assert rejected.status_code == 400, rejected.text
    detail = rejected.json()["detail"]
    assert detail["code"] == "after_sale_insufficient_funds"
    assert detail["shortfall"] == "20.00"

    await db_session.rollback()
    stored = await db_session.execute(
        select(OrderAfterSale.status).where(OrderAfterSale.id == created.json()["id"])
    )
    assert stored.scalar_one() == AfterSaleStatus.PENDING
    booster_after = await _wallet(db_session, booster_id)
    assert booster_after.available_balance == Decimal("0.00")
    assert booster_after.deposit_balance == Decimal("10.00")
    assert (
        await _after_sale_ledger(db_session, order_id=order["id"], user_id=booster_id)
        == []
    )


async def test_admin_approve_adjusted_amount_only_deducts_settled_amount(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """管理员裁定额（20 < 申请 30）低于可用余额：只扣裁定额，保证金不动。"""
    publisher = await _register(
        client, make_captcha, "aftersale-adj-dep@example.com", "PubAdjDep"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await _deposit(client, admin_user, booster_id, "100.00")
    await db_session.rollback()

    created = await _create_after_sale(client, publisher, order["id"], amount="30.00")
    assert created.status_code == 201, created.text
    approved = await _review(
        client,
        admin_user,
        created.json()["id"],
        {"action": "approve", "reason": "协商后部分赔付", "amount": "20.00"},
    )
    assert approved.status_code == 200, approved.text
    # 未动用保证金 → decision_reason 只有裁定说明，不追加抵扣分项
    assert approved.json()["decision_reason"] == "协商后部分赔付；裁定扣款 ¥20.00（申请 ¥30.00）"

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.available_balance == Decimal("80.00")  # 100 - 20
    assert booster_wallet.deposit_balance == Decimal("100.00")  # 保证金不动
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("120.00")

    grouped = await _wallet_ledger_by_type(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(grouped[WalletTransactionType.AFTERSALE_DEDUCTION]) == 1
    assert grouped[WalletTransactionType.AFTERSALE_DEDUCTION][0].amount == Decimal("-20.00")
    # 保证金腿不存在：可用余额够时不写多余的保证金流水
    assert WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION not in grouped


async def test_repeat_accept_conflicts_and_writes_single_ledger_row(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """连点两次同意：第二次 409，且各类流水仍各只有一条（不重复扣款）。"""
    publisher = await _register(
        client, make_captcha, "aftersale-repeat@example.com", "PubRepeat"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    # 可用 10 + 保证金 100，申请 20 → 触发两条腿
    await _fund_wallet(client, admin_user, booster_id, "-90.00")
    await _deposit(client, admin_user, booster_id, "100.00")
    await db_session.rollback()

    created = await _create_after_sale(client, publisher, order["id"], amount="20.00")
    assert created.status_code == 201, created.text
    after_sale_id = created.json()["id"]

    first = await _accept(client, booster_user, order["id"], after_sale_id)
    assert first.status_code == 200, first.text
    second = await _accept(client, booster_user, order["id"], after_sale_id)
    assert second.status_code == 409, second.text
    assert "该售后申请已处理" in second.json()["detail"]

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.available_balance == Decimal("0.00")
    assert booster_wallet.deposit_balance == Decimal("90.00")  # 只扣了 10
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("120.00")

    grouped = await _wallet_ledger_by_type(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(grouped[WalletTransactionType.AFTERSALE_DEDUCTION]) == 1
    assert len(grouped[WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION]) == 1
    # 出款流水记在老板钱包上，打手侧不产生 AFTERSALE_REFUND
    assert WalletTransactionType.AFTERSALE_REFUND not in grouped


async def test_deposit_offset_works_when_deposit_switch_disabled(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """保证金总开关关闭时仍然照扣（否则等于「关开关躲售后赔付」）。"""
    publisher = await _register(
        client, make_captcha, "aftersale-depoff@example.com", "PubDepOff"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await _fund_wallet(client, admin_user, booster_id, "-90.00")  # 可用 10
    await _deposit(client, admin_user, booster_id, "50.00")
    await db_session.rollback()
    # 默认就是关闭态，显式断言一次，避免默认值变化让本用例失去意义
    assert await deposit_service.is_deposit_enabled(db_session) is False

    created = await _create_after_sale(client, publisher, order["id"], amount="20.00")
    assert created.status_code == 201, created.text
    accepted = await _accept(client, booster_user, order["id"], created.json()["id"])
    assert accepted.status_code == 200, accepted.text

    await db_session.rollback()
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.available_balance == Decimal("0.00")
    # 扣款不看开关：保证金照扣 10，剩 40
    assert booster_wallet.deposit_balance == Decimal("40.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("120.00")


async def test_deposit_deduction_does_not_touch_frozen_balance(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """保证金抵扣只动 deposit_balance：frozen_balance（提现/托管/炸单冻结）绝不受影响。"""
    publisher = await _register(
        client, make_captcha, "aftersale-frozen@example.com", "PubFrozen"
    )
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await _fund_wallet(client, admin_user, booster_id, "-90.00")  # 可用 10
    await _deposit(client, admin_user, booster_id, "50.00")
    # 另造一笔提现冻结 5 元：frozen_balance 是共享池，售后绝不能碰
    await db_session.rollback()
    await client.post(
        "/withdrawals",
        json={
            "amount": "5.00",
            "channel": "ALIPAY",
            "account_name": "张三",
            "account_no": "13800000000",
        },
        headers=auth_header(booster_user),
    )
    await db_session.rollback()
    before = await _wallet(db_session, booster_id)
    assert before.frozen_balance == Decimal("5.00")
    assert before.available_balance == Decimal("5.00")

    created = await _create_after_sale(client, publisher, order["id"], amount="20.00")
    assert created.status_code == 201, created.text
    # 可用 5 + 保证金 50 = 55 ≥ 20，可以结案
    accepted = await _accept(client, booster_user, order["id"], created.json()["id"])
    assert accepted.status_code == 200, accepted.text

    await db_session.rollback()
    after = await _wallet(db_session, booster_id)
    assert after.frozen_balance == Decimal("5.00")  # 冻结池分文未动
    assert after.available_balance == Decimal("0.00")  # 可用 5 全扣
    assert after.deposit_balance == Decimal("35.00")  # 保证金补扣 15

    # 且没有写出炸单/取消赔偿两类流水（未越界消费其他冻结）
    ledger = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=booster_id
    )
    types = {row.type for row in ledger}
    assert types == {
        WalletTransactionType.AFTERSALE_DEDUCTION,
        WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION,
    }


async def test_deposit_deduction_to_zero_does_not_break_resolve_tier(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """保证金被扣到 0 后档位解析不崩：掉到最低档（预期行为，不额外报错）。"""
    publisher = await _register(
        client, make_captcha, "aftersale-tier@example.com", "PubTier"
    )
    booster_id = booster_user["user"]["id"]
    order = await _completed_order(client, admin_user, publisher, booster_user)
    await _fund_wallet(client, admin_user, booster_id, "-100.00")  # 可用 0
    await _deposit(client, admin_user, booster_id, "60.00")
    await db_session.rollback()

    tiers = await deposit_service.list_tiers(db_session)
    # 先把标量取出来：API 调用会让会话失效，之后再访问 ORM 属性会触发懒加载
    min_threshold = tiers[0].threshold
    tier_before, balance_before = await deposit_service.get_user_tier(
        db_session, booster_id
    )
    assert balance_before == Decimal("60.00")
    assert tier_before is not None

    created = await _create_after_sale(client, publisher, order["id"], amount="60.00")
    assert created.status_code == 201, created.text
    accepted = await _accept(client, booster_user, order["id"], created.json()["id"])
    assert accepted.status_code == 200, accepted.text

    await db_session.rollback()
    assert (await _deposit_balance(db_session, booster_id)) == Decimal("0.00")
    # 档位解析不抛异常，掉到最低档（门槛 0）
    tier_after, balance_after = await deposit_service.get_user_tier(
        db_session, booster_id
    )
    assert balance_after == Decimal("0.00")
    assert tier_after is not None
    assert tier_after.threshold == min_threshold
    assert tier_after.threshold == Decimal("0.00")
