"""订单服务费结算修复测试（不自动运行；由主代理统一执行）。

覆盖线上 bug：发单按毛额 ``price × max_claims`` 冻结，结单只扣打手净额，
服务费差额永久残留在发布人 ``frozen_balance``。修复后：

- 非管理员发布人结单：托管整单价格被拆成「打手净额 ORDER_PAYMENT」+
  「平台服务费 SERVICE_FEE_SETTLE」，发布人 frozen 归零；
- 服务费写入 platform_income，后台可见；
- 多名额订单每个名额各结一次，互不重复；
- 部分到账时 fee = price - payout；
- 管理员发布（平台单）无托管、不产生平台收入；
- 取消/退款路径仍然全额解冻，不受影响。
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_income import PlatformIncome
from app.models.wallet import WalletTransaction, WalletTransactionType
from tests.conftest import auth_header


async def _register(
    client: AsyncClient, make_captcha, email: str, username: str
) -> dict:
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "Passw0rd123",
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _set_global_fee(client: AsyncClient, admin_user: dict, rate: str) -> None:
    resp = await client.put(
        "/admin/service-fee/settings",
        json={"service_fee_rate": rate},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text


async def _top_up(client: AsyncClient, admin_user: dict, user: dict, amount: str) -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "测试充值"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text


async def _create_order(
    client: AsyncClient,
    publisher: dict,
    *,
    price: str = "100.00",
    max_claims: int = 1,
) -> int:
    resp = await client.post(
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
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _accept_and_deliver(
    client: AsyncClient, booster: dict, order_id: int
) -> None:
    resp = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster)
    )
    assert resp.status_code == 200, resp.text
    resp = await client.put(
        f"/orders/{order_id}/deliver", headers=auth_header(booster)
    )
    assert resp.status_code == 200, resp.text


async def _claim_id(
    client: AsyncClient, reviewer: dict, order_id: int, booster: dict
) -> int:
    resp = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(reviewer)
    )
    assert resp.status_code == 200, resp.text
    for claim in resp.json()["items"]:
        if claim["booster_id"] == booster["user"]["id"]:
            return claim["id"]
    raise AssertionError("未找到打手名额")


async def _review(
    client: AsyncClient,
    reviewer: dict,
    order_id: int,
    claim_id: int,
    *,
    amount: str | None = None,
) -> None:
    payload: dict = {"action": "approve"}
    if amount is not None:
        payload["amount"] = amount
    resp = await client.put(
        f"/orders/{order_id}/claims/{claim_id}/review",
        json=payload,
        headers=auth_header(reviewer),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "SETTLED"


async def _wallet(client: AsyncClient, user: dict) -> dict:
    resp = await client.get("/wallet", headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _income_rows(
    db_session: AsyncSession, order_id: int | None = None
) -> list[PlatformIncome]:
    """读取已提交的平台收入（先结束旧事务，避免快照读到旧数据）。"""
    await db_session.rollback()
    statement = select(PlatformIncome).order_by(PlatformIncome.id)
    if order_id is not None:
        statement = statement.where(PlatformIncome.order_id == order_id)
    result = await db_session.execute(statement)
    return list(result.scalars().all())


async def _fee_transactions(
    db_session: AsyncSession, order_id: int | None = None
) -> list[WalletTransaction]:
    await db_session.rollback()
    statement = (
        select(WalletTransaction)
        .where(WalletTransaction.type == WalletTransactionType.SERVICE_FEE_SETTLE)
        .order_by(WalletTransaction.id)
    )
    if order_id is not None:
        statement = statement.where(WalletTransaction.order_id == order_id)
    result = await db_session.execute(statement)
    return list(result.scalars().all())


async def test_single_claim_fee_clears_publisher_frozen(
    client: AsyncClient,
    admin_user: dict,
    make_captcha,
    db_session: AsyncSession,
):
    """8% 单名额：发单人 frozen 归零、平台收入=服务费、打手到账=净额。"""
    await _set_global_fee(client, admin_user, "8")
    publisher = await _register(
        client, make_captcha, "fee.pub@example.com", "FeePublisher"
    )
    booster = await _register(
        client, make_captcha, "fee.boost@example.com", "FeeBooster"
    )
    await _top_up(client, admin_user, publisher, "1000.00")

    order_id = await _create_order(client, publisher, price="100.00", max_claims=1)
    await _accept_and_deliver(client, booster, order_id)

    claim_id = await _claim_id(client, publisher, order_id, booster)
    await _review(client, publisher, order_id, claim_id)

    publisher_wallet = await _wallet(client, publisher)
    assert Decimal(str(publisher_wallet["available_balance"])) == Decimal("900.00")
    assert Decimal(str(publisher_wallet["frozen_balance"])) == Decimal("0.00")

    booster_wallet = await _wallet(client, booster)
    assert Decimal(str(booster_wallet["available_balance"])) == Decimal("92.00")
    assert Decimal(str(booster_wallet["frozen_balance"])) == Decimal("0.00")

    incomes = await _income_rows(db_session, order_id)
    assert len(incomes) == 1
    assert Decimal(str(incomes[0].amount)) == Decimal("8.00")
    assert incomes[0].booster_id == booster["user"]["id"]
    assert Decimal(str(incomes[0].service_fee_rate)) == Decimal("8.00")

    fee_txs = await _fee_transactions(db_session, order_id)
    assert len(fee_txs) == 1
    assert Decimal(str(fee_txs[0].amount)) == Decimal("-8.00")
    # 余额快照仍是可用余额，不因只扣冻结而变化
    assert Decimal(str(fee_txs[0].balance_before)) == Decimal(
        str(fee_txs[0].balance_after)
    )


async def test_two_claim_order_settles_fee_per_claim(
    client: AsyncClient,
    admin_user: dict,
    make_captcha,
    db_session: AsyncSession,
):
    """多名额（2）两单都结算后 frozen==0，且平台收入 2 条。"""
    await _set_global_fee(client, admin_user, "8")
    publisher = await _register(
        client, make_captcha, "fee2.pub@example.com", "FeePublisher2"
    )
    booster_a = await _register(
        client, make_captcha, "fee2.a@example.com", "FeeBoosterA"
    )
    booster_b = await _register(
        client, make_captcha, "fee2.b@example.com", "FeeBoosterB"
    )
    await _top_up(client, admin_user, publisher, "1000.00")

    order_id = await _create_order(client, publisher, price="100.00", max_claims=2)
    await _accept_and_deliver(client, booster_a, order_id)
    await _accept_and_deliver(client, booster_b, order_id)

    for booster in (booster_a, booster_b):
        claim_id = await _claim_id(client, publisher, order_id, booster)
        await _review(client, publisher, order_id, claim_id)

    publisher_wallet = await _wallet(client, publisher)
    assert Decimal(str(publisher_wallet["available_balance"])) == Decimal("800.00")
    assert Decimal(str(publisher_wallet["frozen_balance"])) == Decimal("0.00")

    for booster in (booster_a, booster_b):
        wallet = await _wallet(client, booster)
        assert Decimal(str(wallet["available_balance"])) == Decimal("92.00")

    incomes = await _income_rows(db_session, order_id)
    assert len(incomes) == 2
    assert {income.booster_id for income in incomes} == {
        booster_a["user"]["id"],
        booster_b["user"]["id"],
    }
    assert sum(Decimal(str(income.amount)) for income in incomes) == Decimal("16.00")

    fee_txs = await _fee_transactions(db_session, order_id)
    assert len(fee_txs) == 2
    assert sorted(str(tx.amount) for tx in fee_txs) == ["-8.00", "-8.00"]

    resp = await client.get(f"/orders/{order_id}", headers=auth_header(publisher))
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"


async def test_partial_payout_fee_is_price_minus_payout(
    client: AsyncClient,
    admin_user: dict,
    make_captcha,
    db_session: AsyncSession,
):
    """管理员按 60 部分到账：fee == price - payout == 40。"""
    await _set_global_fee(client, admin_user, "8")
    publisher = await _register(
        client, make_captcha, "fee3.pub@example.com", "FeePublisher3"
    )
    booster = await _register(
        client, make_captcha, "fee3.boost@example.com", "FeeBooster3"
    )
    await _top_up(client, admin_user, publisher, "1000.00")

    order_id = await _create_order(client, publisher, price="100.00", max_claims=1)
    await _accept_and_deliver(client, booster, order_id)

    claim_id = await _claim_id(client, admin_user, order_id, booster)
    await _review(client, admin_user, order_id, claim_id, amount="60.00")

    publisher_wallet = await _wallet(client, publisher)
    assert Decimal(str(publisher_wallet["available_balance"])) == Decimal("900.00")
    assert Decimal(str(publisher_wallet["frozen_balance"])) == Decimal("0.00")

    booster_wallet = await _wallet(client, booster)
    assert Decimal(str(booster_wallet["available_balance"])) == Decimal("60.00")

    incomes = await _income_rows(db_session, order_id)
    assert len(incomes) == 1
    assert Decimal(str(incomes[0].amount)) == Decimal("40.00")

    fee_txs = await _fee_transactions(db_session, order_id)
    assert len(fee_txs) == 1
    assert Decimal(str(fee_txs[0].amount)) == Decimal("-40.00")


async def test_admin_published_order_has_no_platform_income(
    client: AsyncClient,
    admin_user: dict,
    make_captcha,
    db_session: AsyncSession,
):
    """管理员发布（无托管）不产生 platform_income / 服务费流水。"""
    await _set_global_fee(client, admin_user, "8")
    booster = await _register(
        client, make_captcha, "fee4.boost@example.com", "FeeBooster4"
    )

    order_id = await _create_order(client, admin_user, price="100.00", max_claims=1)
    await _accept_and_deliver(client, booster, order_id)

    claim_id = await _claim_id(client, admin_user, order_id, booster)
    await _review(client, admin_user, order_id, claim_id)

    booster_wallet = await _wallet(client, booster)
    assert Decimal(str(booster_wallet["available_balance"])) == Decimal("92.00")

    assert await _income_rows(db_session, order_id) == []
    assert await _fee_transactions(db_session, order_id) == []


async def test_cancel_still_releases_full_escrow(
    client: AsyncClient,
    admin_user: dict,
    make_captcha,
    db_session: AsyncSession,
):
    """取消订单仍全额解冻，不写平台收入、不产生服务费流水。"""
    await _set_global_fee(client, admin_user, "8")
    publisher = await _register(
        client, make_captcha, "fee5.pub@example.com", "FeePublisher5"
    )
    await _top_up(client, admin_user, publisher, "1000.00")

    order_id = await _create_order(client, publisher, price="100.00", max_claims=1)

    resp = await client.put(
        f"/orders/{order_id}/cancel", headers=auth_header(publisher)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CANCELLED"

    publisher_wallet = await _wallet(client, publisher)
    assert Decimal(str(publisher_wallet["available_balance"])) == Decimal("1000.00")
    assert Decimal(str(publisher_wallet["frozen_balance"])) == Decimal("0.00")

    assert await _income_rows(db_session, order_id) == []
    assert await _fee_transactions(db_session, order_id) == []
