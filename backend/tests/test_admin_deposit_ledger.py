"""后台保证金划转流水测试（2026-11-12）。

覆盖：
1. 默认查询北京时间今天，只返回 DEPOSIT_TRANSFER_IN/OUT；
2. 转入/转回汇总（金额、笔数、转回人数、净变化）；
3. direction / 用户关键词 / 分页 / 日期区间筛选；
4. 管理员权限、非法日期范围；
5. 管理员手动调保证金时记录 operator，列表能区分自助与后台操作。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.product_time import product_local_date
from app.models.wallet import WalletTransaction, WalletTransactionType
from app.services.wallet_service import get_wallet_service
from tests.conftest import auth_header


async def _fund_wallet(db_session, user_id: int, amount: str = "1000.00"):
    """给测试用户充可用余额（直接改库，不走充值网关）。"""
    service = get_wallet_service(db_session)
    wallet = await service.get_or_create_wallet(user_id)
    wallet.available_balance = Decimal(amount)
    await db_session.commit()
    return wallet


async def _seed_transfers(db_session, user_a: dict, user_b: dict, admin: dict):
    """A 转入 100、转回 30；B 转入 50；返回三笔流水。"""
    service = get_wallet_service(db_session)
    wallet_a = await _fund_wallet(db_session, user_a["user"]["id"])
    wallet_b = await _fund_wallet(db_session, user_b["user"]["id"])

    tx_in_a = await service.transfer_to_deposit(wallet_a, amount=Decimal("100.00"))
    await db_session.commit()
    tx_out_a = await service.transfer_from_deposit(wallet_a, amount=Decimal("30.00"))
    await db_session.commit()
    tx_in_b = await service.transfer_to_deposit(wallet_b, amount=Decimal("50.00"))
    await db_session.commit()

    # 管理员手动补 20：类型仍是转入，但带 operator_id，列表要能识别
    tx_admin = await service.adjust_deposit(
        wallet_a,
        delta=Decimal("20.00"),
        tx_type=WalletTransactionType.DEPOSIT_TRANSFER_IN,
        operator_id=admin["user"]["id"],
        remark="管理员补保证金",
    )
    await db_session.commit()
    return tx_in_a, tx_out_a, tx_in_b, tx_admin


def _amount(item: dict) -> Decimal:
    return Decimal(str(item["deposit_change"]))


async def test_ledger_defaults_to_today_and_summarizes(
    client, admin_user, booster_user, registered_user, db_session
):
    """默认今天：3 笔用户划转 + 1 笔管理员调整；汇总为 170 转入 / 30 转回。"""
    await _seed_transfers(db_session, booster_user, registered_user, admin_user)

    resp = await client.get(
        "/admin/deposit/transactions", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 4
    assert data["pages"] == 1
    assert data["start_date"] == data["end_date"]

    stats = data["stats"]
    assert Decimal(str(stats["in_amount"])) == Decimal("170.00")
    assert stats["in_count"] == 3
    assert Decimal(str(stats["out_amount"])) == Decimal("30.00")
    assert stats["out_count"] == 1
    assert stats["out_users"] == 1
    assert Decimal(str(stats["self_out_amount"])) == Decimal("30.00")
    assert stats["self_out_count"] == 1
    assert stats["self_out_users"] == 1
    assert Decimal(str(stats["net_amount"])) == Decimal("140.00")

    # 转回那条：保证金变动为负、可用余额变动为正
    out_items = [item for item in data["items"] if item["direction"] == "out"]
    assert len(out_items) == 1
    assert _amount(out_items[0]) == Decimal("-30.00")
    assert Decimal(str(out_items[0]["available_change"])) == Decimal("30.00")

    # 管理员调整那条能被识别：只动保证金，可用余额变动必须为 0
    admin_items = [item for item in data["items"] if item["operator_id"]]
    assert len(admin_items) == 1
    assert admin_items[0]["operator_username"] == admin_user["user"]["username"]
    assert admin_items[0]["remark"] == "管理员补保证金"
    assert Decimal(str(admin_items[0]["available_change"])) == Decimal("0.00")
    assert _amount(admin_items[0]) == Decimal("20.00")


async def test_ledger_direction_and_search(
    client, admin_user, booster_user, registered_user, db_session
):
    """direction 与用户关键词可组合；汇总不随 direction 变化。"""
    await _seed_transfers(db_session, booster_user, registered_user, admin_user)

    out_resp = await client.get(
        "/admin/deposit/transactions?direction=out",
        headers=auth_header(admin_user),
    )
    assert out_resp.status_code == 200
    out_data = out_resp.json()
    assert out_data["total"] == 1
    assert all(item["direction"] == "out" for item in out_data["items"])
    assert Decimal(str(out_data["stats"]["in_amount"])) == Decimal("170.00")

    in_resp = await client.get(
        "/admin/deposit/transactions?direction=in",
        headers=auth_header(admin_user),
    )
    assert in_resp.status_code == 200
    assert in_resp.json()["total"] == 3

    q_resp = await client.get(
        f"/admin/deposit/transactions?q={booster_user['user']['username']}",
        headers=auth_header(admin_user),
    )
    assert q_resp.status_code == 200
    q_data = q_resp.json()
    assert q_data["total"] == 3  # A 的转入 + 转回 + 管理员调整
    assert Decimal(str(q_data["stats"]["in_amount"])) == Decimal("120.00")
    assert Decimal(str(q_data["stats"]["out_amount"])) == Decimal("30.00")


async def test_ledger_pagination_and_yesterday_empty(
    client, admin_user, booster_user, registered_user, db_session
):
    """分页与北京时间日期边界：今天的流水不能被算进昨天。"""
    await _seed_transfers(db_session, booster_user, registered_user, admin_user)

    page_resp = await client.get(
        "/admin/deposit/transactions?page=1&page_size=2",
        headers=auth_header(admin_user),
    )
    assert page_resp.status_code == 200
    page_data = page_resp.json()
    assert len(page_data["items"]) == 2
    assert page_data["total"] == 4
    assert page_data["pages"] == 2

    today = product_local_date(datetime.now(timezone.utc))
    yesterday = today - timedelta(days=1)
    yesterday_resp = await client.get(
        f"/admin/deposit/transactions?start_date={yesterday.isoformat()}"
        f"&end_date={yesterday.isoformat()}",
        headers=auth_header(admin_user),
    )
    assert yesterday_resp.status_code == 200
    assert yesterday_resp.json()["total"] == 0
    assert Decimal(str(yesterday_resp.json()["stats"]["in_amount"])) == Decimal("0.00")


async def test_ledger_rejects_invalid_range_and_non_admin(
    client, admin_user, booster_user
):
    """结束日期早于开始日期 400；普通打手 403。"""
    bad_range = await client.get(
        "/admin/deposit/transactions?start_date=2026-11-12&end_date=2026-11-11",
        headers=auth_header(admin_user),
    )
    assert bad_range.status_code == 400

    denied = await client.get(
        "/admin/deposit/transactions", headers=auth_header(booster_user)
    )
    assert denied.status_code == 403


async def test_ledger_only_reads_transfer_types(
    client, admin_user, booster_user, db_session
):
    """炸单/取消赔偿等扣款不属于「转入转出」，不能混进这个页面。"""
    service = get_wallet_service(db_session)
    wallet = await _fund_wallet(db_session, booster_user["user"]["id"])
    # 写一条不属于划转类型的流水（订单收入）
    await service._apply(  # noqa: SLF001 - 测试直接造账
        wallet,
        tx_type=WalletTransactionType.ORDER_INCOME,
        amount=Decimal("50.00"),
        available_delta=Decimal("50.00"),
        remark="订单收入",
    )
    await db_session.commit()

    resp = await client.get(
        "/admin/deposit/transactions", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["items"] == []

    # 确保里确实只有目标类型：直接查表辅助断言
    types = (
        await db_session.execute(
            select(WalletTransaction.type).where(
                WalletTransaction.wallet_id == wallet.id
            )
        )
    ).scalars().all()
    assert WalletTransactionType.ORDER_INCOME in types


async def test_self_out_stats_exclude_admin_adjustment(
    client, admin_user, booster_user, db_session
):
    """“用户退保证金”统计不含管理员手动扣减，但总量仍包含。"""
    service = get_wallet_service(db_session)
    wallet = await _fund_wallet(db_session, booster_user["user"]["id"])
    await service.transfer_to_deposit(wallet, amount=Decimal("100.00"))
    await db_session.commit()
    await service.transfer_from_deposit(wallet, amount=Decimal("30.00"))
    await db_session.commit()
    # 管理员再手动扣 10：type 仍是 DEPOSIT_TRANSFER_OUT，但 operator_id 非空
    await service.adjust_deposit(
        wallet,
        delta=Decimal("-10.00"),
        tx_type=WalletTransactionType.DEPOSIT_TRANSFER_OUT,
        operator_id=admin_user["user"]["id"],
        remark="管理员扣减",
    )
    await db_session.commit()

    resp = await client.get(
        "/admin/deposit/transactions", headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    assert Decimal(str(stats["out_amount"])) == Decimal("40.00")
    assert stats["out_count"] == 2
    assert stats["out_users"] == 1
    assert Decimal(str(stats["self_out_amount"])) == Decimal("30.00")
    assert stats["self_out_count"] == 1
    assert stats["self_out_users"] == 1
