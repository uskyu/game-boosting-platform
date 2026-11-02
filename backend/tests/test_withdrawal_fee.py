"""提现手续费：费率快照、冻结口径、实际打款金额。"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.withdrawal_rule_service import get_or_create_withdrawal_rule_setting
from tests.conftest import auth_header


async def _fund_wallet(client: AsyncClient, admin_user: dict, user_id: int, amount: str) -> None:
    resp = await client.post(
        f"/admin/wallets/{user_id}/adjust",
        json={"amount": amount, "reason": "测试入账"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200


async def _set_fee_rate(db_session: AsyncSession, rate: str) -> None:
    rule = await get_or_create_withdrawal_rule_setting(db_session)
    rule.withdrawal_fee_rate = Decimal(rate)
    await db_session.commit()


async def test_withdrawal_fee_snapshot_and_net_payout(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
    db_session: AsyncSession,
):
    """5% 费率：申请 40 → 手续费 2、实际到账 38，冻结仍是申请金额 40。"""
    await _set_fee_rate(db_session, "5.00")
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "100.00")

    resp = await client.post(
        "/withdrawals",
        json={
            "amount": "40.00",
            "channel": "ALIPAY",
            "account_name": "张三",
            "account_no": "13800000000",
        },
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert Decimal(str(data["fee_rate"])) == Decimal("5.00")
    assert Decimal(str(data["fee_amount"])) == Decimal("2.00")
    assert Decimal(str(data["net_amount"])) == Decimal("38.00")

    wallet = (await client.get("/wallet", headers=auth_header(registered_user))).json()
    assert Decimal(str(wallet["available_balance"])) == Decimal("60.00")
    assert Decimal(str(wallet["frozen_balance"])) == Decimal("40.00")

    quota = (
        await client.get("/withdrawals/quota", headers=auth_header(registered_user))
    ).json()
    assert Decimal(str(quota["withdrawal_fee_rate"])) == Decimal("5.00")

    # 打款批次按实际到账金额汇总，导出/回执匹配都走 net_amount。
    batch_resp = await client.post(
        "/admin/withdrawals/payout-batches",
        json={"ids": [data["id"]], "category": "ALIPAY"},
        headers=auth_header(admin_user),
    )
    assert batch_resp.status_code == 201, batch_resp.text
    batch = batch_resp.json()
    assert Decimal(str(batch["total_amount"])) == Decimal("38.00")
    item = batch["items"][0]
    assert Decimal(str(item["amount"])) == Decimal("40.00")
    assert Decimal(str(item["fee_amount"])) == Decimal("2.00")
    assert Decimal(str(item["net_amount"])) == Decimal("38.00")


async def test_withdrawal_fee_zero_keeps_legacy_amount(
    client: AsyncClient,
    registered_user: dict,
    admin_user: dict,
    db_session: AsyncSession,
):
    """费率为 0 时手续费 0、实际到账=申请金额，保持旧行为。"""
    await _set_fee_rate(db_session, "0.00")
    user_id = registered_user["user"]["id"]
    await _fund_wallet(client, admin_user, user_id, "100.00")

    resp = await client.post(
        "/withdrawals",
        json={
            "amount": "40.00",
            "channel": "ALIPAY",
            "account_name": "张三",
            "account_no": "13800000000",
        },
        headers=auth_header(registered_user),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert Decimal(str(data["fee_amount"])) == Decimal("0.00")
    assert Decimal(str(data["net_amount"])) == Decimal("40.00")
