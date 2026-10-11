"""取消/争议期间停倒计时 + 处理完回重传态 + 撤销争议护栏 + 打手可扣款项。

覆盖 2026-10-11 服务层改动：

1. 取消协商挂起（create_cancel_request）→ 名额 settlement_paused=True，
   调度器候选不再包含该名额；恢复计时后才正常到期结算；
2. 协商被拒绝（decide_cancel_request reject）→ 协商期间提交的 DELIVERED
   名额回 CLAIMED、approved_at/due_at 清空、订单回 LOCKED、paused 已清；
3. 发起争议（dispute_order）→ pre_dispute_status 记原值、名额暂停计时；
4. 撤销争议（cancel_dispute）→ 四类护栏各 400；prev=LOCKED 恢复 LOCKED；
   prev=DELIVERED 走交付重置（名额 CLAIMED、订单 LOCKED、due_at 清空）；
5. 打手端 my_claim / 报名名单 / 我的报名三条路径的
   booster_compensation_available 口径一致（冻结赔付+可用余额+保证金）。

测试库为本地 game_boosting_test（conftest 自动建库/重置表）。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import (
    ClaimLifecycleStatus,
    Order,
    OrderClaim,
    OrderStatus,
    PaymentStatus,
)
from app.models.user import User, UserRole
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from app.services.payout_scheduler import scan_due_payouts
from tests.conftest import auth_header

BOOSTER_PASSWORD = "Passw0rd123"


# ---------------------------------------------------------------------------
# 夹具助手（沿用 test_accept_order_race.py 的注册/建单/接单写法）
# ---------------------------------------------------------------------------


async def _register_booster(
    client: AsyncClient,
    db_session: AsyncSession,
    make_captcha,
    email: str,
    username: str,
) -> dict:
    """Register a user, promote to BOOSTER via DB, re-login for the new role."""
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": BOOSTER_PASSWORD,
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.role = UserRole.BOOSTER
    user.booster_quota = 5
    await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": email, "password": BOOSTER_PASSWORD}
    )
    assert login.status_code == 200, login.text
    return login.json()


async def _register(
    client: AsyncClient, make_captcha, email: str, username: str
) -> dict:
    """Register a plain (non-booster) user and return the login payload."""
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "TestPass123",
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    login = await client.post(
        "/auth/login", json={"email": email, "password": "TestPass123"}
    )
    assert login.status_code == 200, login.text
    return login.json()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: int) -> None:
    response = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "test fund"},
        headers=auth_header(admin_user),
    )
    assert response.status_code in (200, 201), response.text


async def _deposit(
    client: AsyncClient, admin_user: dict, user: dict, amount: int
) -> None:
    response = await client.post(
        f"/admin/users/{user['user']['id']}/adjust-deposit",
        json={"delta": amount, "remark": "test deposit"},
        headers=auth_header(admin_user),
    )
    assert response.status_code in (200, 201), response.text


async def _publish(
    client: AsyncClient,
    publisher: dict,
    *,
    max_claims: int = 1,
    compensation_amount: str | None = None,
    notes: str | None = None,
) -> dict:
    payload: dict = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": "100.00",
        "max_claims": max_claims,
    }
    if compensation_amount is not None:
        payload["compensation_amount"] = compensation_amount
    if notes is not None:
        payload["notes"] = notes
    response = await client.post(
        "/orders/create", json=payload, headers=auth_header(publisher)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _accept(client: AsyncClient, booster: dict, order_id: int) -> None:
    response = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster)
    )
    assert response.status_code == 200, response.text


async def _deliver(
    client: AsyncClient, booster: dict, order_id: int, note: str = "已完成"
) -> None:
    response = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": note},
        headers=auth_header(booster),
    )
    assert response.status_code == 200, response.text


async def _claim_id(
    client: AsyncClient, publisher: dict, order_id: int, booster_id: int
) -> int:
    response = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(publisher)
    )
    assert response.status_code == 200, response.text
    return next(
        item["id"]
        for item in response.json()["items"]
        if item["booster_id"] == booster_id
    )


async def _create_request(
    client: AsyncClient,
    order_id: int,
    claim_id: int,
    *,
    reason: str,
    compensation_amount: float,
    user: dict,
):
    return await client.post(
        f"/orders/{order_id}/cancel-requests",
        json={
            "claim_id": claim_id,
            "reason": reason,
            "compensation_amount": compensation_amount,
        },
        headers=auth_header(user),
    )


async def _decide(
    client: AsyncClient,
    order_id: int,
    request_id: int,
    *,
    action: str,
    user: dict,
    decision_reason: str | None = None,
):
    return await client.post(
        f"/orders/{order_id}/cancel-requests/{request_id}/decision",
        json={"action": action, "decision_reason": decision_reason},
        headers=auth_header(user),
    )


async def _dispute(
    client: AsyncClient, order_id: int, user: dict, reason: str
):
    return await client.put(
        f"/orders/{order_id}/dispute",
        params={"reason": reason},
        headers=auth_header(user),
    )


async def _cancel_dispute(client: AsyncClient, order_id: int, admin: dict):
    return await client.put(
        f"/admin/orders/{order_id}/dispute/cancel", headers=auth_header(admin)
    )


async def _order_row(db: AsyncSession, order_id: int) -> Order:
    """Re-read the order row outside any stale transaction snapshot.

    rollback 结束 REPEATABLE READ 快照，expire_all 丢掉 identity map 里的
    旧属性（API 请求在别的 session 里提交后必须重新加载，否则读到旧值）。
    """
    await db.rollback()
    db.expire_all()
    result = await db.execute(select(Order).where(Order.id == order_id))
    return result.scalar_one()


async def _claim_row(db: AsyncSession, order_id: int, booster_id: int) -> OrderClaim:
    await db.rollback()
    db.expire_all()
    result = await db.execute(
        select(OrderClaim).where(
            OrderClaim.order_id == order_id, OrderClaim.booster_id == booster_id
        )
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# 需求 5：取消/争议期间停倒计时
# ---------------------------------------------------------------------------


async def test_cancel_request_pauses_settlement_and_scheduler_skips_claim(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """发起取消协商后名额计时暂停，调度器候选不再包含它。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "pause_a@example.com", "PauseA"
    )
    booster_id = booster["user"]["id"]
    order = await _publish(client, admin_user, max_claims=1)
    order_id = order["id"]
    await _accept(client, booster, order_id)
    await _deliver(client, booster, order_id, note="结单了")
    claim_id = await _claim_id(client, admin_user, order_id, booster_id)

    # 让名额「已到期」：订单到账时效设为 0，交付时间推到 1 小时前
    # （MySQL DATETIME 秒级精度会把 delivered_at 取整，必须留出余量，
    # 否则 due 可能只比扫描时刻晚几毫秒而偶发不结算）
    row = await _order_row(db_session, order_id)
    row.payout_delay_days = 0
    row.payout_delay_hours = 0
    await db_session.commit()
    claim = await _claim_row(db_session, order_id, booster_id)
    claim.delivered_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.commit()

    created = await _create_request(
        client,
        order_id,
        claim_id,
        reason="双方协商取消测试",
        compensation_amount=0,
        user=admin_user,
    )
    assert created.status_code == 200, created.text

    # 名额倒计时已暂停，但交付状态不变（仍待审核）
    claim = await _claim_row(db_session, order_id, booster_id)
    assert claim.settlement_paused is True
    assert claim.status == ClaimLifecycleStatus.DELIVERED
    assert claim.settled_at is None

    # 调度器不把暂停的名额当候选：即使 due 已过也不自动结算
    settled = await scan_due_payouts(db_session)
    assert claim_id not in settled
    claim = await _claim_row(db_session, order_id, booster_id)
    assert claim.status == ClaimLifecycleStatus.DELIVERED
    assert claim.settled_at is None


async def test_scheduler_skips_paused_claim_and_settles_after_resume(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """调度器过滤契约：settlement_paused=True 的名额被跳过，置回 False 后正常结算。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "sched_a@example.com", "SchedA"
    )
    booster_id = booster["user"]["id"]
    order = await _publish(client, admin_user, max_claims=1)
    order_id = order["id"]
    await _accept(client, booster, order_id)
    await _deliver(client, booster, order_id, note="结单了")
    claim_id = await _claim_id(client, admin_user, order_id, booster_id)

    # 已到期（到账时效 0 + 交付时间推到 1 小时前，避开 DATETIME 秒级
    # 取整带来的亚秒竞争）+ 手动暂停（无取消协商，单独钉住调度器过滤条件）
    row = await _order_row(db_session, order_id)
    row.payout_delay_days = 0
    row.payout_delay_hours = 0
    await db_session.commit()
    claim = await _claim_row(db_session, order_id, booster_id)
    claim.delivered_at = datetime.now(timezone.utc) - timedelta(hours=1)
    claim.settlement_paused = True
    await db_session.commit()

    settled = await scan_due_payouts(db_session)
    assert claim_id not in settled
    claim = await _claim_row(db_session, order_id, booster_id)
    assert claim.status == ClaimLifecycleStatus.DELIVERED
    assert claim.settled_at is None

    # 恢复计时：同一名额立即被结算（证明跳过是暂停条件在生效）
    claim = await _claim_row(db_session, order_id, booster_id)
    claim.settlement_paused = False
    await db_session.commit()
    settled = await scan_due_payouts(db_session)
    assert claim_id in settled
    claim = await _claim_row(db_session, order_id, booster_id)
    assert claim.status == ClaimLifecycleStatus.SETTLED
    assert claim.settled_at is not None


async def test_decide_reject_resets_delivered_claim_and_resumes_timer(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """协商被拒绝：协商期间提交的 DELIVERED 名额回 CLAIMED、approved_at 清空、
    订单回 LOCKED、倒计时恢复。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "reject_a@example.com", "RejectA"
    )
    booster_id = booster["user"]["id"]
    order = await _publish(client, admin_user, max_claims=1)
    order_id = order["id"]
    await _accept(client, booster, order_id)
    await _deliver(client, booster, order_id, note="交付汇报")
    claim_id = await _claim_id(client, admin_user, order_id, booster_id)

    # 模拟 AFTER_APPROVAL 已审核通过的名额（approved_at + due_at 均在）
    row = await _order_row(db_session, order_id)
    row.delivery_note = "订单级交付备注"
    row.delivery_attachments = [{"url": "/uploads/deliveries/legacy.png"}]
    await db_session.commit()
    claim = await _claim_row(db_session, order_id, booster_id)
    claim.approved_at = datetime.now(timezone.utc)
    claim.settlement_due_at = datetime.now(timezone.utc) + timedelta(hours=2)
    await db_session.commit()

    created = await _create_request(
        client,
        order_id,
        claim_id,
        reason="老板想协商取消",
        compensation_amount=0,
        user=admin_user,
    )
    assert created.status_code == 200, created.text

    # 打手（接收方）拒绝
    decision = await _decide(
        client,
        order_id,
        created.json()["id"],
        action="reject",
        user=booster,
        decision_reason="我会继续完成",
    )
    assert decision.status_code == 200, decision.text

    claim = await _claim_row(db_session, order_id, booster_id)
    assert claim.status == ClaimLifecycleStatus.CLAIMED
    assert claim.approved_at is None
    assert claim.delivered_at is None
    assert claim.settlement_due_at is None
    assert claim.delivery_rejection_reason == "取消协商被拒绝，请重新提交结单"
    assert claim.settlement_paused is False

    row = await _order_row(db_session, order_id)
    assert row.status == OrderStatus.LOCKED
    # 订单级交付字段同步清空（order.booster_id == claim.booster_id）
    assert row.delivery_note is None
    assert row.delivery_attachments is None


# ---------------------------------------------------------------------------
# 需求 4：争议前状态快照 + 暂停
# ---------------------------------------------------------------------------


async def test_dispute_order_snapshots_pre_dispute_status_and_pauses(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """发起争议：pre_dispute_status 记下原值，名额倒计时暂停。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "dispute_a@example.com", "DisputeA"
    )
    booster_id = booster["user"]["id"]
    order = await _publish(client, admin_user, max_claims=1)
    order_id = order["id"]
    await _accept(client, booster, order_id)

    resp = await _dispute(client, order_id, admin_user, "打手长时间失联")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DISPUTED"

    row = await _order_row(db_session, order_id)
    assert row.pre_dispute_status == OrderStatus.LOCKED
    assert row.status == OrderStatus.DISPUTED
    assert (row.notes or "").startswith("争议原因: 打手长时间失联")

    claim = await _claim_row(db_session, order_id, booster_id)
    assert claim.settlement_paused is True


# ---------------------------------------------------------------------------
# 需求 4：撤销争议护栏
# ---------------------------------------------------------------------------


async def test_cancel_dispute_rejects_when_not_in_dispute(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """护栏 1：订单不在争议中 → 400。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "cd_1a@example.com", "CdOneA"
    )
    order = await _publish(client, admin_user, max_claims=1)
    order_id = order["id"]
    await _accept(client, booster, order_id)

    resp = await _cancel_dispute(client, order_id, admin_user)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "订单不在争议中"


async def test_cancel_dispute_rejects_when_refunded(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """护栏 2：资金已退款 → 400。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "cd_2a@example.com", "CdTwoA"
    )
    order = await _publish(client, admin_user, max_claims=1)
    order_id = order["id"]
    await _accept(client, booster, order_id)
    assert (await _dispute(client, order_id, admin_user, "争议中")).status_code == 200

    row = await _order_row(db_session, order_id)
    row.payment_status = PaymentStatus.REFUNDED
    await db_session.commit()

    resp = await _cancel_dispute(client, order_id, admin_user)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "资金已退款，无法撤销争议"


async def test_cancel_dispute_rejects_when_claim_settled(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """护栏 3：已有结算记录（SETTLED 名额）→ 400。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "cd_3a@example.com", "CdThreeA"
    )
    booster_id = booster["user"]["id"]
    order = await _publish(client, admin_user, max_claims=1)
    order_id = order["id"]
    await _accept(client, booster, order_id)
    assert (await _dispute(client, order_id, admin_user, "争议中")).status_code == 200

    claim = await _claim_row(db_session, order_id, booster_id)
    claim.status = ClaimLifecycleStatus.SETTLED
    await db_session.commit()

    resp = await _cancel_dispute(client, order_id, admin_user)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "已有结算记录，无法撤销争议"


async def test_cancel_dispute_rejects_when_funds_posted(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """护栏 4：订单维度资金已落账（钱包流水）→ 400。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "cd_4a@example.com", "CdFourA"
    )
    order = await _publish(client, admin_user, max_claims=1)
    order_id = order["id"]
    await _accept(client, booster, order_id)
    assert (await _dispute(client, order_id, admin_user, "争议中")).status_code == 200

    # 造一条该订单的托管解冻流水（管理员发单无托管，这里直插账本）
    await db_session.rollback()
    wallet = Wallet(
        user_id=admin_user["user"]["id"],
        available_balance=Decimal("100.00"),
        frozen_balance=Decimal("0.00"),
        deposit_balance=Decimal("0.00"),
        total_income=Decimal("0.00"),
        total_withdrawn=Decimal("0.00"),
    )
    db_session.add(wallet)
    await db_session.flush()
    db_session.add(
        WalletTransaction(
            wallet_id=wallet.id,
            type=WalletTransactionType.ESCROW_RELEASE,
            amount=Decimal("100.00"),
            balance_before=Decimal("0.00"),
            balance_after=Decimal("100.00"),
            order_id=order_id,
        )
    )
    await db_session.commit()

    resp = await _cancel_dispute(client, order_id, admin_user)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "资金已落账，无法撤销争议"


# ---------------------------------------------------------------------------
# 需求 4：撤销争议后的状态恢复
# ---------------------------------------------------------------------------


async def test_cancel_dispute_restores_locked_and_strips_dispute_note(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """争议前为 LOCKED：撤销后回 LOCKED、备注里的「争议原因」首行被剥掉。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "cd_5a@example.com", "CdFiveA"
    )
    booster_id = booster["user"]["id"]
    order = await _publish(client, admin_user, max_claims=1, notes="老板原始备注")
    order_id = order["id"]
    await _accept(client, booster, order_id)
    assert (
        await _dispute(client, order_id, admin_user, "协商前先争议")
    ).status_code == 200

    resp = await _cancel_dispute(client, order_id, admin_user)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "LOCKED"

    row = await _order_row(db_session, order_id)
    assert row.status == OrderStatus.LOCKED
    assert row.pre_dispute_status is None
    assert row.notes == "老板原始备注"

    claim = await _claim_row(db_session, order_id, booster_id)
    assert claim.settlement_paused is False


async def test_cancel_dispute_resets_delivered_claim_when_pre_dispute_delivered(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """争议前为 DELIVERED（含 DELIVERED 名额）：撤销后走交付重置——
    名额回 CLAIMED、due_at 清空、订单回 LOCKED。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "cd_6a@example.com", "CdSixA"
    )
    booster_id = booster["user"]["id"]
    order = await _publish(client, admin_user, max_claims=1)
    order_id = order["id"]
    await _accept(client, booster, order_id)
    await _deliver(client, booster, order_id, note="交付了")

    # 旧单名额制聚合状态：订单 DELIVERED + 名额 DELIVERED
    row = await _order_row(db_session, order_id)
    row.status = OrderStatus.DELIVERED
    await db_session.commit()

    assert (
        await _dispute(client, order_id, admin_user, "待查的交付")
    ).status_code == 200
    row = await _order_row(db_session, order_id)
    assert row.pre_dispute_status == OrderStatus.DELIVERED

    resp = await _cancel_dispute(client, order_id, admin_user)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "LOCKED"

    claim = await _claim_row(db_session, order_id, booster_id)
    assert claim.status == ClaimLifecycleStatus.CLAIMED
    assert claim.delivered_at is None
    assert claim.settlement_due_at is None
    assert claim.approved_at is None
    assert claim.delivery_rejection_reason == "争议已撤销，请重新提交结单"
    assert claim.settlement_paused is False

    row = await _order_row(db_session, order_id)
    assert row.status == OrderStatus.LOCKED
    assert row.pre_dispute_status is None


# ---------------------------------------------------------------------------
# 需求 9：打手端可扣款项只显示保证金 → 冻结+可用+保证金
# ---------------------------------------------------------------------------


async def test_booster_compensation_available_consistent_across_paths(
    client: AsyncClient,
    admin_user: dict,
    db_session: AsyncSession,
    make_captcha,
):
    """my_claim（订单详情/交付回包）、报名名单、我的报名三条路径的
    booster_compensation_available 一致 = 冻结赔付 + 可用余额 + 保证金。"""
    publisher = await _register(
        client, make_captcha, "comp_pub@example.com", "CompPub"
    )
    publisher_id = publisher["user"]["id"]
    booster = await _register_booster(
        client, db_session, make_captcha, "comp_a@example.com", "CompA"
    )
    booster_id = booster["user"]["id"]

    await _fund(client, admin_user, publisher, 200)
    await _fund(client, admin_user, booster, 100)
    await _deposit(client, admin_user, booster, 50)
    # 炸单赔付 ¥20：接单即冻结（DEPOSIT_HOLD）
    order = await _publish(client, publisher, compensation_amount="20.00")
    order_id = order["id"]
    await _accept(client, booster, order_id)

    # 冻结 20 + 可用 80 + 保证金 50 = 150
    detail = await client.get(f"/orders/{order_id}", headers=auth_header(booster))
    assert detail.status_code == 200, detail.text
    my_claim = detail.json()["my_claim"]
    assert my_claim is not None
    assert Decimal(my_claim["booster_compensation_available"]) == Decimal("150.00")

    claims = await client.get(
        f"/orders/{order_id}/claims", headers=auth_header(publisher)
    )
    assert claims.status_code == 200, claims.text
    list_item = next(
        item
        for item in claims.json()["items"]
        if item["booster_id"] == booster_id
    )
    assert Decimal(list_item["booster_compensation_available"]) == Decimal("150.00")

    mine = await client.get("/orders/claims/mine", headers=auth_header(booster))
    assert mine.status_code == 200, mine.text
    mine_item = next(
        item for item in mine.json()["items"] if item["order"]["id"] == order_id
    )
    assert Decimal(mine_item["booster_compensation_available"]) == Decimal("150.00")

    # 三条路径口径一致（防止再次出现「我的报名只显示保证金」）
    assert (
        Decimal(my_claim["booster_compensation_available"])
        == Decimal(list_item["booster_compensation_available"])
        == Decimal(mine_item["booster_compensation_available"])
    )
    assert publisher_id == order["user_id"]

