"""后台「支付记录」接口测试：全量查询、搜索筛选与人工改判状态机。

覆盖重点（钱袋规则，逐条对应需求）：

- 非管理员访问一律 403，普通用户看不到别人的充值单；
- 列表能按状态筛选、按商户单号/用户搜索，统计条不受筛选影响；
- **PENDING → 成功**：入账一次，钱包 +金额，流水记 RECHARGE；
- **重复置成功**：拒绝，绝不重复加钱（并发兜底由行锁 + 唯一键保证）；
- **PENDING → 失败**：只改状态，**绝不动钱**（本来没入账，扣钱就是白扣）；
- **SUCCESS → 失败**：冲正扣回，流水记 RECHARGE_REVERSAL，余额回零；
- **SUCCESS → 失败但余额已被花掉**：拒绝，不产生负余额、不写半截流水；
- **FAILED → 成功**：纠错补回，金额再次入账；
- 改判失败必须填原因；回调入账 !== 人工改判（marked_by/marked_at 可区分）；
- 改判后用户收到 RECHARGE_UPDATED 通知。
"""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.notification import Notification, NotificationType
from app.models.recharge import RechargeOrder, RechargeStatus
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from app.services import epay_service
from tests.conftest import auth_header

PAY_ADDRESS = "https://pay.example.com"
EPAY_ID = "1001"
EPAY_KEY = "unit-test-secret"
RECORDS = "/admin/payment/recharges"


def _settings_payload() -> dict:
    return {
        "enabled": True,
        "pay_address": PAY_ADDRESS,
        "epay_id": EPAY_ID,
        "epay_key": EPAY_KEY,
        "alipay_enabled": True,
        "wxpay_enabled": True,
        "min_amount": 1,
    }


async def _configure(client: AsyncClient, admin_user: dict) -> None:
    resp = await client.put(
        "/admin/payment/settings",
        json=_settings_payload(),
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 200, resp.text


def _notify_params(trade_no: str, money: str) -> dict[str, str]:
    return epay_service.generate_params(
        {
            "pid": EPAY_ID,
            "trade_no": "EPAY-1",
            "out_trade_no": trade_no,
            "type": "alipay",
            "name": "账户充值",
            "money": money,
            "trade_status": "TRADE_SUCCESS",
        },
        EPAY_KEY,
    )


async def _create_recharge(
    client: AsyncClient, user: dict, amount: float = 100
) -> dict:
    resp = await client.post(
        "/wallet/recharge",
        json={"amount": amount, "payment_method": "alipay"},
        headers=auth_header(user),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _pay(client: AsyncClient, trade_no: str, amount: str = "100.00") -> None:
    """走真实易支付回调完成入账（模拟上游回调成功）。"""
    resp = await client.post(
        "/wallet/recharge/notify", data=_notify_params(trade_no, amount)
    )
    assert resp.text == "success", resp.text


async def _recharge_row(db_session, trade_no: str) -> RechargeOrder:
    # MySQL 默认 REPEATABLE READ：先关掉可能已开启的只读事务，下一次查询
    # 才会看到 API 请求（另一条连接）刚刚提交的写入。
    await db_session.commit()
    result = await db_session.execute(
        select(RechargeOrder).where(RechargeOrder.trade_no == trade_no)
    )
    return result.scalar_one()


async def _wallet(db_session, user_id: int) -> Wallet:
    # 同上：钱包可能是被 API 请求刚创建的，需要新事务才能看到
    await db_session.commit()
    result = await db_session.execute(select(Wallet).where(Wallet.user_id == user_id))
    return result.scalar_one()


async def _wallet_or_none(db_session, user_id: int) -> Wallet | None:
    """钱包可能压根还没创建（PENDING 充值单不动钱包）。"""
    await db_session.commit()
    result = await db_session.execute(select(Wallet).where(Wallet.user_id == user_id))
    return result.scalar_one_or_none()


async def _balance(db_session, user_id: int) -> Decimal:
    """用户可用余额；钱包不存在即 0.00。"""
    wallet = await _wallet_or_none(db_session, user_id)
    if wallet is None:
        return Decimal("0.00")
    await db_session.refresh(wallet)
    return Decimal(str(wallet.available_balance))


async def _set_status(
    client: AsyncClient,
    admin_user: dict,
    recharge_id: int,
    status: str,
    reason: str | None = None,
):
    body = {"status": status}
    if reason is not None:
        body["reason"] = reason
    return await client.post(
        f"{RECORDS}/{recharge_id}/status",
        json=body,
        headers=auth_header(admin_user),
    )


def _user_id(payload: dict) -> int:
    return payload["user"]["id"]


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------


async def test_list_requires_admin(client: AsyncClient, registered_user: dict):
    resp = await client.get(RECORDS, headers=auth_header(registered_user))
    assert resp.status_code == 403


async def test_list_requires_login(client: AsyncClient):
    resp = await client.get(RECORDS)
    assert resp.status_code == 401


async def test_update_status_requires_admin(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user)
    row = await client.get(RECORDS, headers=auth_header(admin_user))
    recharge_id = row.json()["items"][0]["id"]
    resp = await _set_status(client, registered_user, recharge_id, "SUCCESS")
    assert resp.status_code == 403
    # 越权请求不得改动任何东西
    assert (await client.get("/wallet", headers=auth_header(registered_user))).json()[
        "available_balance"
    ] == "0.00"


# ---------------------------------------------------------------------------
# 列表 / 筛选 / 统计
# ---------------------------------------------------------------------------


async def test_list_shows_all_users_recharges(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
):
    await _configure(client, admin_user)
    mine = await _create_recharge(client, registered_user, amount=30)
    other = await _create_recharge(client, booster_user, amount=50)

    resp = await client.get(RECORDS, headers=auth_header(admin_user))
    assert resp.status_code == 200
    data = resp.json()
    trade_nos = {item["trade_no"] for item in data["items"]}
    assert trade_nos == {mine["trade_no"], other["trade_no"]}
    assert data["total"] == 2
    assert data["stats"]["total"] == 2
    assert data["stats"]["pending"] == 2
    assert data["stats"]["success"] == 0
    # 用户信息随列表带出，管理员不用再逐个查 ID
    users = {item["user"]["username"] for item in data["items"]}
    assert users == {"TestUser", "TestBooster"}


async def test_list_filter_by_status(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    await _configure(client, admin_user)
    paid = await _create_recharge(client, registered_user, amount=10)
    await _pay(client, paid["trade_no"], "10.00")
    await _create_recharge(client, registered_user, amount=20)  # 仍是 PENDING

    resp = await client.get(
        RECORDS, params={"status": "SUCCESS"}, headers=auth_header(admin_user)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["trade_no"] == paid["trade_no"]
    # 统计条仍是全量口径：切筛选不影响顶部数字
    assert data["stats"]["total"] == 2
    assert data["stats"]["success"] == 1
    assert data["stats"]["pending"] == 1


async def test_list_search_by_trade_no_and_username(
    client: AsyncClient, admin_user: dict, registered_user: dict, booster_user: dict
):
    await _configure(client, admin_user)
    mine = await _create_recharge(client, registered_user, amount=10)
    await _create_recharge(client, booster_user, amount=20)

    by_trade = await client.get(
        RECORDS, params={"q": mine["trade_no"]}, headers=auth_header(admin_user)
    )
    assert by_trade.json()["total"] == 1
    assert by_trade.json()["items"][0]["trade_no"] == mine["trade_no"]

    by_name = await client.get(
        RECORDS, params={"q": "TestBooster"}, headers=auth_header(admin_user)
    )
    assert by_name.json()["total"] == 1
    assert by_name.json()["items"][0]["user"]["username"] == "TestBooster"


async def test_list_rejects_unknown_status(
    client: AsyncClient, admin_user: dict
):
    resp = await client.get(
        RECORDS, params={"status": "NOT_A_STATUS"}, headers=auth_header(admin_user)
    )
    assert resp.status_code == 400


async def test_list_exposes_notify_payload_and_epay_trade_no(
    client: AsyncClient, admin_user: dict, registered_user: dict
):
    """「回调是否成功」要能一眼看出：上游单号 + 回调原文都要在列表里。"""
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=10)
    await _pay(client, created["trade_no"], "10.00")

    data = (await client.get(RECORDS, headers=auth_header(admin_user))).json()
    item = data["items"][0]
    assert item["status"] == "SUCCESS"
    assert item["epay_trade_no"] == "EPAY-1"
    assert "TRADE_SUCCESS" in item["notify_payload"]
    # 回调入账不是人工改判
    assert item["marked_at"] is None
    assert item["marked_by"] is None


# ---------------------------------------------------------------------------
# 改判成功
# ---------------------------------------------------------------------------


async def test_pending_to_success_credits_wallet(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session,
):
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=88)
    user_id = _user_id(registered_user)

    row = await _recharge_row(db_session, created["trade_no"])
    assert row.status == RechargeStatus.PENDING

    resp = await _set_status(client, admin_user, row.id, "SUCCESS", "客户已付款，补单")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["marked_at"] is not None
    assert data["marked_by"]["username"] == "TestAdmin"
    assert data["admin_note"] == "客户已付款，补单"

    wallet = await _wallet(db_session, user_id)
    assert Decimal(str(wallet.available_balance)) == Decimal("88.00")
    # 充值不是收入
    assert Decimal(str(wallet.total_income)) == Decimal("0.00")

    # 刷新一次看落库结果（响应对象可能早于 commit）
    row = await _recharge_row(db_session, created["trade_no"])
    await db_session.refresh(row)
    assert row.status == RechargeStatus.SUCCESS
    assert row.paid_at is not None


async def test_success_twice_is_rejected_and_does_not_double_credit(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session,
):
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=100)
    user_id = _user_id(registered_user)
    row = await _recharge_row(db_session, created["trade_no"])

    first = await _set_status(client, admin_user, row.id, "SUCCESS")
    assert first.status_code == 200
    second = await _set_status(client, admin_user, row.id, "SUCCESS")
    assert second.status_code == 400
    assert "已是成功状态" in second.json()["detail"]

    wallet = await _wallet(db_session, user_id)
    assert Decimal(str(wallet.available_balance)) == Decimal("100.00")


async def test_callback_success_then_manual_success_rejected(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    """回调已入账的单，管理员再点成功不能重复加钱。"""
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=100)
    await _pay(client, created["trade_no"], "100.00")

    row = await _recharge_row(db_session, created["trade_no"])
    resp = await _set_status(client, admin_user, row.id, "SUCCESS")
    assert resp.status_code == 400
    wallet = await _wallet(db_session, _user_id(registered_user))
    assert Decimal(str(wallet.available_balance)) == Decimal("100.00")


# ---------------------------------------------------------------------------
# 改判失败
# ---------------------------------------------------------------------------


async def test_pending_to_failed_does_not_touch_money(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session,
):
    """PENDING 本来没入账，改失败**绝不能扣钱**（否则是白扣用户自己的钱）。"""
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=100)
    user_id = _user_id(registered_user)
    row = await _recharge_row(db_session, created["trade_no"])

    resp = await _set_status(client, admin_user, row.id, "FAILED", "上游未收到款")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "FAILED"

    # 钱包可能根本没被创建过——这正是「没动钱」的最强证据
    assert await _balance(db_session, user_id) == Decimal("0.00")
    wallet = await _wallet_or_none(db_session, user_id)
    if wallet is not None:
        entries = (
            await db_session.execute(
                select(WalletTransaction).where(
                    WalletTransaction.wallet_id == wallet.id
                )
            )
        ).scalars().all()
        assert len(entries) == 0


async def test_failed_requires_reason(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=10)
    row = await _recharge_row(db_session, created["trade_no"])

    resp = await _set_status(client, admin_user, row.id, "FAILED")
    assert resp.status_code == 422
    # 空串同样不放过
    resp = await _set_status(client, admin_user, row.id, "FAILED", "   ")
    assert resp.status_code == 422


async def test_success_to_failed_reverses_balance(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session,
):
    """已入账的单改失败：冲正扣回，余额归零，另记一条 RECHARGE_REVERSAL。"""
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=100)
    await _pay(client, created["trade_no"], "100.00")
    user_id = _user_id(registered_user)
    row = await _recharge_row(db_session, created["trade_no"])

    wallet = await _wallet(db_session, user_id)
    assert Decimal(str(wallet.available_balance)) == Decimal("100.00")

    resp = await _set_status(
        client, admin_user, row.id, "FAILED", "上游退款，撤销到账"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "FAILED"
    assert resp.json()["admin_note"] == "上游退款，撤销到账"

    # 用新事务重读余额与流水（本会话的快照看不到 API 请求的提交）
    assert await _balance(db_session, user_id) == Decimal("0.00")
    wallet = await _wallet(db_session, user_id)
    assert Decimal(str(wallet.total_income)) == Decimal("0.00")

    types = (
        await db_session.execute(
            select(WalletTransaction.type).where(
                WalletTransaction.wallet_id == wallet.id
            )
        )
    ).scalars().all()
    assert WalletTransactionType.RECHARGE in types
    assert WalletTransactionType.RECHARGE_REVERSAL in types


async def test_success_to_failed_rejected_when_balance_spent(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session,
):
    """钱已被花掉时冲正必须拒绝：不允许负余额，也不允许静默截断。"""
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=100)
    await _pay(client, created["trade_no"], "100.00")
    user_id = _user_id(registered_user)

    # 模拟用户已把钱花掉大部分
    wallet = await _wallet(db_session, user_id)
    wallet.available_balance = Decimal("5.00")
    await db_session.commit()

    row = await _recharge_row(db_session, created["trade_no"])
    resp = await _set_status(client, admin_user, row.id, "FAILED", "撤销")
    assert resp.status_code == 400
    assert "可用余额不足" in resp.json()["detail"]

    await db_session.refresh(wallet)
    # 余额一分没动，状态也没改
    assert Decimal(str(wallet.available_balance)) == Decimal("5.00")
    row = await _recharge_row(db_session, created["trade_no"])
    await db_session.refresh(row)
    assert row.status == RechargeStatus.SUCCESS


# ---------------------------------------------------------------------------
# 纠错：失败 → 成功
# ---------------------------------------------------------------------------


async def test_failed_to_success_credits_again(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session,
):
    """置失败后发现上游其实到账了：能改回成功并把钱补上。"""
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=100)
    user_id = _user_id(registered_user)
    row = await _recharge_row(db_session, created["trade_no"])

    assert (await _set_status(client, admin_user, row.id, "FAILED", "误判")).status_code == 200
    assert (await _set_status(client, admin_user, row.id, "SUCCESS", "对账确认已到账")).status_code == 200

    wallet = await _wallet(db_session, user_id)
    assert Decimal(str(wallet.available_balance)) == Decimal("100.00")


async def test_success_failed_success_cycle_keeps_books_balanced(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    db_session,
):
    """来回改判三次：余额与流水条数必须严格对得上，不能重复加钱。"""
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=60)
    user_id = _user_id(registered_user)
    row = await _recharge_row(db_session, created["trade_no"])

    assert (await _set_status(client, admin_user, row.id, "SUCCESS")).status_code == 200
    assert (
        await _set_status(client, admin_user, row.id, "FAILED", "撤销")
    ).status_code == 200
    assert (await _set_status(client, admin_user, row.id, "SUCCESS")).status_code == 200

    wallet = await _wallet(db_session, user_id)
    assert Decimal(str(wallet.available_balance)) == Decimal("60.00")

    entries = (
        await db_session.execute(
            select(WalletTransaction).where(WalletTransaction.wallet_id == wallet.id)
        )
    ).scalars().all()
    # 两次入账 + 一次冲正
    assert len(entries) == 3
    signed = sum(Decimal(str(entry.amount)) for entry in entries)
    assert signed == Decimal("60.00")


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------


async def test_update_rejects_closed_order(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    """用户侧已关闭的单不再接受后台改判（没钱可动，改判只会制造脏数据）。"""
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=10)
    row = await _recharge_row(db_session, created["trade_no"])
    row.status = RechargeStatus.CLOSED
    await db_session.commit()

    for target in ("SUCCESS", "FAILED"):
        resp = await _set_status(client, admin_user, row.id, target, "随便")
        assert resp.status_code == 400
        assert "关闭" in resp.json()["detail"]


async def test_update_unknown_recharge_returns_404(
    client: AsyncClient, admin_user: dict
):
    resp = await _set_status(client, admin_user, 999999, "SUCCESS")
    assert resp.status_code == 404


async def test_update_rejects_invalid_target_status(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=10)
    row = await _recharge_row(db_session, created["trade_no"])

    resp = await client.post(
        f"{RECORDS}/{row.id}/status",
        json={"status": "CLOSED"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 用户通知
# ---------------------------------------------------------------------------


async def _notifications(db_session, user_id: int) -> list[Notification]:
    """读该用户收到的支付改判通知。

    先 commit 结束当前只读事务：MySQL 默认 REPEATABLE READ，同一事务内
    看不到 API 请求（另一条连接）随后提交的行。
    """
    await db_session.commit()
    result = await db_session.execute(
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.type == NotificationType.RECHARGE_UPDATED,
        )
        .order_by(Notification.id)
    )
    return list(result.scalars().all())


async def test_manual_decision_notifies_user(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    """人工改动余额必须让用户端看得到，不允许静默改钱。"""
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=45)
    user_id = _user_id(registered_user)
    row = await _recharge_row(db_session, created["trade_no"])

    assert (
        await _set_status(client, admin_user, row.id, "SUCCESS", "客户已付款")
    ).status_code == 200

    notifications = await _notifications(db_session, user_id)
    assert len(notifications) == 1
    note = notifications[0]
    assert note.title == "充值已到账"
    assert "45.00" in note.content
    assert "客户已付款" in note.content
    assert note.ref_id == row.id

    # 冲正后再来一条撤销通知
    assert (
        await _set_status(client, admin_user, row.id, "FAILED", "上游退款")
    ).status_code == 200
    notifications = await _notifications(db_session, user_id)
    assert len(notifications) == 2
    revoked = [n for n in notifications if n.title == "充值已撤销"]
    assert len(revoked) == 1
    assert "45.00" in revoked[0].content
    assert "上游退款" in revoked[0].content


async def test_pending_to_failed_sends_no_money_notification(
    client: AsyncClient, admin_user: dict, registered_user: dict, db_session
):
    """没动钱就不该发「余额已变动」的通知，否则用户会以为钱被扣了。"""
    await _configure(client, admin_user)
    created = await _create_recharge(client, registered_user, amount=45)
    row = await _recharge_row(db_session, created["trade_no"])

    assert (
        await _set_status(client, admin_user, row.id, "FAILED", "上游未收到款")
    ).status_code == 200
    assert await _notifications(db_session, _user_id(registered_user)) == []
