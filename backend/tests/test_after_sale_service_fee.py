"""售后结案时平台承担已收服务费的定向测试。

口径：订单结单时打手只拿净额 ``price × (1 - 费率)``，平台已收取服务费。
售后结案时退款中对应服务费的部分由平台承担（平台收入记负向
``AFTERSALE_FEE_REFUND``），老板收到完整售后金额，打手只退实际拿到的净额；
打手净额不足时仍按既有口径 400，平台不替打手垫付本金。

布置沿用 ``tests/test_after_sales.py`` 的真实流程：发布托管 → 接单 → 交付 →
老板确认；服务费通过平台全局设置烙进订单。
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType
from app.models.order_after_sale import AfterSaleStatus, OrderAfterSale
from app.models.platform_income import PlatformIncome
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from app.services import platform_income_service
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


async def _set_global_fee(client: AsyncClient, admin_user: dict, rate: str) -> None:
    response = await client.put(
        "/admin/service-fee/settings",
        json={"service_fee_rate": rate},
        headers=auth_header(admin_user),
    )
    assert response.status_code == 200, response.text


async def _fund_wallet(
    client: AsyncClient, admin_user: dict, user_id: int, amount: str
) -> None:
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
    client: AsyncClient, publisher: dict, order_id: int, amount: str
):
    return await client.post(
        f"/orders/{order_id}/after-sales",
        json={
            "reason": "打手未按约定时间完成，要求返还部分费用",
            "amount": amount,
            "images": [],
        },
        headers=auth_header(publisher),
    )


async def _review(
    client: AsyncClient, admin_user: dict, after_sale_id: int, body: dict
):
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


async def _platform_rows(db: AsyncSession, order_id: int) -> list[PlatformIncome]:
    """读取该订单的平台收入流水（先结束旧快照）。"""
    await db.rollback()
    result = await db.execute(
        select(PlatformIncome)
        .where(PlatformIncome.order_id == order_id)
        .order_by(PlatformIncome.id)
    )
    return list(result.scalars().all())


async def _after_sale_ledger(
    db: AsyncSession, *, order_id: int, user_id: int
) -> list[WalletTransaction]:
    await db.rollback()
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


async def _resolved_notifications(db: AsyncSession) -> dict[int, str]:
    db.expire_all()
    result = await db.execute(
        select(Notification).where(
            Notification.type == NotificationType.AFTERSALES_RESOLVED
        )
    )
    return {row.user_id: row.content for row in result.scalars().all()}


async def test_full_after_sale_platform_bears_collected_service_fee(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """8% / ¥100：结单打手 92；全额售后 100 → 打手扣 92、老板 +100、平台净 0。

    修复前打手可用只有 92，按 100 全额扣款会因缺 8 元返回 400；本用例
    必须成功，并留下 SERVICE_FEE +8 与 AFTERSALE_FEE_REFUND -8 两条平台收入。
    """
    await _set_global_fee(client, admin_user, "8")
    publisher = await _register(
        client, make_captcha, "asfee-full@example.com", "AsFeeFull"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]

    order = await _completed_order(client, admin_user, publisher, booster_user)
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("92.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("100.00")

    created = await _create_after_sale(client, publisher, order["id"], "100.00")
    assert created.status_code == 201, created.text
    approved = await _review(
        client, admin_user, created.json()["id"], {"action": "approve"}
    )
    assert approved.status_code == 200, approved.text
    data = approved.json()
    assert data["status"] == "APPROVED"
    assert "平台承担手续费 ¥8.00" in data["decision_reason"]

    # 打手只退净额 92，老板收到完整 100。
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("0.00")
    assert (
        await _wallet(db_session, publisher_id)
    ).available_balance == Decimal("200.00")

    booster_ledger = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(booster_ledger) == 1
    assert booster_ledger[0].type == WalletTransactionType.AFTERSALE_DEDUCTION
    assert booster_ledger[0].amount == Decimal("-92.00")
    assert booster_ledger[0].balance_before == Decimal("92.00")
    assert booster_ledger[0].balance_after == Decimal("0.00")

    refunds = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=publisher_id
    )
    assert len(refunds) == 1
    assert refunds[0].type == WalletTransactionType.AFTERSALE_REFUND
    assert refunds[0].amount == Decimal("100.00")
    assert refunds[0].balance_before == Decimal("100.00")
    assert refunds[0].balance_after == Decimal("200.00")
    assert "平台承担手续费 ¥8.00" in (refunds[0].remark or "")

    # 平台收入：已收 +8，售后承担 -8；累计与本月都净 0。
    rows = await _platform_rows(db_session, order["id"])
    by_source = {row.source: row for row in rows}
    assert len(rows) == 2
    assert by_source[platform_income_service.SOURCE_SERVICE_FEE].amount == Decimal("8.00")
    refund_row = by_source[platform_income_service.SOURCE_AFTERSALE_FEE_REFUND]
    assert refund_row.amount == Decimal("-8.00")
    assert refund_row.service_fee_rate == Decimal("8.00")
    assert refund_row.booster_id == booster_id

    total, month_total = await platform_income_service.totals(db_session)
    assert total == Decimal("0.00")
    assert month_total == Decimal("0.00")

    listed, count = await platform_income_service.list_income(
        db_session, page=1, page_size=20
    )
    assert count == 2
    assert {record.source for record, *_ in listed} == {
        platform_income_service.SOURCE_SERVICE_FEE,
        platform_income_service.SOURCE_AFTERSALE_FEE_REFUND,
    }

    # 通知分视角：打手看到实扣 92，老板看到完整 100，并注明平台承担。
    by_user = await _resolved_notifications(db_session)
    assert "已扣划 ¥92.00 给老板" in by_user[booster_id]
    assert "平台承担手续费 ¥8.00" in by_user[booster_id]
    assert "已收款 ¥100.00" in by_user[publisher_id]
    assert "平台承担手续费 ¥8.00" in by_user[publisher_id]


async def test_partial_after_sale_platform_bears_proportional_fee(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """部分售后 30：打手 -27.60、老板 +30、平台承担 -2.40。"""
    await _set_global_fee(client, admin_user, "8")
    publisher = await _register(
        client, make_captcha, "asfee-part@example.com", "AsFeePart"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]

    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"], "30.00")
    assert created.status_code == 201, created.text
    approved = await _review(
        client, admin_user, created.json()["id"], {"action": "approve"}
    )
    assert approved.status_code == 200, approved.text
    assert "平台承担手续费 ¥2.40" in approved.json()["decision_reason"]

    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("64.40")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("130.00")

    booster_ledger = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(booster_ledger) == 1
    assert booster_ledger[0].amount == Decimal("-27.60")

    rows = await _platform_rows(db_session, order["id"])
    by_source = {row.source: row for row in rows}
    assert len(rows) == 2
    assert by_source[platform_income_service.SOURCE_AFTERSALE_FEE_REFUND].amount == (
        Decimal("-2.40")
    )
    assert by_source[platform_income_service.SOURCE_AFTERSALE_FEE_REFUND].service_fee_rate == (
        Decimal("8.00")
    )

    total, _month_total = await platform_income_service.totals(db_session)
    assert total == Decimal("5.60")  # 已收 8.00 - 承担 2.40


async def test_cancelled_order_without_income_keeps_booster_liable(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """取消单没有 ORDER_INCOME：platform_share=0，仍按全额从打手扣。"""
    publisher = await _register(
        client, make_captcha, "asfee-cancel@example.com", "AsFeeCancel"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]

    await _fund_wallet(client, admin_user, publisher_id, "200.00")
    order = await _create_order(client, publisher)
    accepted = await client.put(
        f"/orders/{order['id']}/accept", headers=auth_header(booster_user)
    )
    assert accepted.status_code == 200, accepted.text
    cancelled = await client.put(
        f"/admin/orders/{order['id']}/intervene",
        json={"action": "CANCELLED", "reason": "测试取消"},
        headers=auth_header(admin_user),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    # 取消单打手没有结算入账，先充 50 用于售后扣款。
    await _fund_wallet(client, admin_user, booster_id, "50.00")

    created = await _create_after_sale(client, publisher, order["id"], "30.00")
    assert created.status_code == 201, created.text
    approved = await _review(
        client, admin_user, created.json()["id"], {"action": "approve"}
    )
    assert approved.status_code == 200, approved.text
    assert "平台承担手续费" not in (approved.json()["decision_reason"] or "")

    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("20.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal("230.00")

    booster_ledger = await _after_sale_ledger(
        db_session, order_id=order["id"], user_id=booster_id
    )
    assert len(booster_ledger) == 1
    assert booster_ledger[0].amount == Decimal("-30.00")
    # 没有结单入账，也就不存在平台承担与任何平台收入。
    assert await _platform_rows(db_session, order["id"]) == []


async def test_insufficient_booster_net_still_400_and_no_platform_advance(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """打手把 92 用掉后全额售后：仍 400，平台不垫付，售后保持 PENDING。"""
    await _set_global_fee(client, admin_user, "8")
    publisher = await _register(
        client, make_captcha, "asfee-insuf@example.com", "AsFeeInsuf"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]

    order = await _completed_order(client, admin_user, publisher, booster_user)
    # 模拟打手把 92 净额用掉（管理员调账），可用余额归零。
    await _fund_wallet(client, admin_user, booster_id, "-92.00")
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("0.00")
    publisher_before = (await _wallet(db_session, publisher_id)).available_balance

    created = await _create_after_sale(client, publisher, order["id"], "100.00")
    assert created.status_code == 201, created.text
    after_sale_id = created.json()["id"]
    rejected = await _review(client, admin_user, after_sale_id, {"action": "approve"})
    assert rejected.status_code == 400, rejected.text
    detail = rejected.json()["detail"]
    assert detail["code"] == "after_sale_insufficient_funds"
    # required 保留原语义（完整售后金额），并补充打手应扣与平台承担。
    assert detail["required"] == "100.00"
    assert detail["booster_required"] == "92.00"
    assert detail["platform_share"] == "8.00"
    assert detail["shortfall"] == "92.00"
    assert "本次扣款 ¥92.00" in detail["message"]

    # 售后仍 PENDING、双方余额零变动、没有平台负向收入。
    await db_session.rollback()
    stored = await db_session.execute(
        select(OrderAfterSale.status).where(OrderAfterSale.id == after_sale_id)
    )
    assert stored.scalar_one() == AfterSaleStatus.PENDING
    await db_session.rollback()
    assert (await _wallet(db_session, booster_id)).available_balance == Decimal("0.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == publisher_before

    rows = await _platform_rows(db_session, order["id"])
    assert [row.source for row in rows] == [
        platform_income_service.SOURCE_SERVICE_FEE
    ]
    assert rows[0].amount == Decimal("8.00")


async def test_repeat_approve_does_not_duplicate_platform_refund(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """重复同意不重复写平台负向收入（服务层也保持幂等）。"""
    await _set_global_fee(client, admin_user, "8")
    publisher = await _register(
        client, make_captcha, "asfee-repeat@example.com", "AsFeeRepeat"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]

    order = await _completed_order(client, admin_user, publisher, booster_user)
    created = await _create_after_sale(client, publisher, order["id"], "100.00")
    assert created.status_code == 201, created.text
    after_sale_id = created.json()["id"]

    first = await _review(client, admin_user, after_sale_id, {"action": "approve"})
    assert first.status_code == 200, first.text
    second = await _review(client, admin_user, after_sale_id, {"action": "approve"})
    assert second.status_code == 409, second.text

    await db_session.rollback()
    rows = await db_session.execute(
        select(PlatformIncome).where(
            PlatformIncome.order_id == order["id"],
            PlatformIncome.source
            == platform_income_service.SOURCE_AFTERSALE_FEE_REFUND,
        )
    )
    assert len(list(rows.scalars().all())) == 1

    # 服务层再写一次同键记录：返回 None，不新增行。
    await db_session.rollback()
    duplicate = await platform_income_service.record_aftersale_fee_borne(
        db_session,
        order_id=order["id"],
        booster_id=booster_id,
        amount=Decimal("8.00"),
        rate=Decimal("8.00"),
    )
    assert duplicate is None
    await db_session.rollback()
    rows = await db_session.execute(
        select(PlatformIncome).where(
            PlatformIncome.order_id == order["id"],
            PlatformIncome.source
            == platform_income_service.SOURCE_AFTERSALE_FEE_REFUND,
        )
    )
    assert len(list(rows.scalars().all())) == 1

    # 老板只收到一次完整退款。
    assert (
        await _wallet(db_session, publisher_id)
    ).available_balance == Decimal("200.00")
