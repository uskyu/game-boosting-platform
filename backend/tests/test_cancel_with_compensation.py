"""发单员申请取消订单（POST /orders/{id}/apply-cancel）测试。

产品语义（老板拍板）：发单员对**进行中**的订单直接发起「申请取消」，填理由
（≥3 字）+ 接单人保证金扣除金额（默认 0）。提交即生效，**无审批流**：订单
取消、托管与赔偿金解冻、按金额从每个活跃接单人保证金直扣、等额补偿入发单员
可用余额、通知打手。

覆盖重点：
- 门槛：非发单员 403、非进行中 400、原因过短 400、扣款超过接单人保证金 400、
  无活跃接单人时扣款 400；
- 成功路径：订单与名额取消、approved_deduction / approved_note 落库、
  打手保证金扣除与发单员赔偿入账两条流水金额相等、notes 前置取消原因；
- 0 元扣款不产生任何取消赔偿流水；
- 多接单按活跃人数逐人扣除、入账等额放大；
- 每个活跃接单人落一条取消通知。
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select

from app.models.notification import Notification, NotificationType
from app.models.order import (
    ClaimLifecycleStatus,
    ClaimStatus,
    Order,
    OrderClaim,
    OrderStatus,
)
from app.models.user import User, UserRole
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from tests.conftest import auth_header

DEDUCT = WalletTransactionType.CANCEL_COMPENSATION_DEDUCT
COMPENSATE_IN = WalletTransactionType.CANCEL_COMPENSATION_IN


# ---------------------------------------------------------------------------
# 测试脚手架
# ---------------------------------------------------------------------------


async def _register(
    client: AsyncClient, make_captcha, email: str, username: str
) -> dict:
    """注册并登录一个普通用户（返回带 access_token 的登录响应）。"""
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


async def _promote_booster(db_session, email: str) -> None:
    """把注册用户提升为 BOOSTER（与 conftest.booster_user 同款处理）。"""
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.role = UserRole.BOOSTER
    user.booster_quota = 5
    await db_session.commit()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount) -> None:
    """管理员给用户加可用余额（发单托管需要钱）。"""
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": float(amount), "reason": "test fund"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


async def _set_deposit(client: AsyncClient, admin_user: dict, user: dict, amount) -> None:
    """管理员给用户打入保证金余额（独立余额池，不走可用余额）。"""
    resp = await client.post(
        f"/admin/users/{user['user']['id']}/adjust-deposit",
        json={"delta": float(amount), "remark": "test deposit"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


async def _publish(
    client: AsyncClient, user: dict, *, price: str, max_claims: int = 1
) -> dict:
    """以发单员身份发布订单（调用方需先充值可用余额）。"""
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": price,
            "max_claims": max_claims,
        },
        headers=auth_header(user),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _accept(client: AsyncClient, user: dict, order_id: int) -> None:
    resp = await client.put(f"/orders/{order_id}/accept", headers=auth_header(user))
    assert resp.status_code == 200, resp.text


async def _wallet(db_session, user_id: int) -> Wallet:
    """直接读库拿钱包（应用侧请求已提交，先清缓存避免读到旧快照）。"""
    db_session.expire_all()
    result = await db_session.execute(
        select(Wallet).where(Wallet.user_id == user_id)
    )
    wallet = result.scalar_one_or_none()
    assert wallet is not None, f"user {user_id} has no wallet"
    return wallet


async def _transactions(
    db_session, *, user_id: int, tx_type: WalletTransactionType
) -> list[WalletTransaction]:
    """某用户的某类钱包流水（按钱包 join 查询）。"""
    db_session.expire_all()
    result = await db_session.execute(
        select(WalletTransaction)
        .join(Wallet, WalletTransaction.wallet_id == Wallet.id)
        .where(Wallet.user_id == user_id, WalletTransaction.type == tx_type)
    )
    return list(result.scalars().all())


async def _order_claims(db_session, order_id: int) -> list[OrderClaim]:
    db_session.expire_all()
    result = await db_session.execute(
        select(OrderClaim).where(OrderClaim.order_id == order_id)
    )
    return list(result.scalars().all())


async def _db_order(db_session, order_id: int) -> Order:
    db_session.expire_all()
    order = await db_session.get(Order, order_id)
    assert order is not None
    return order


async def _apply_cancel(client: AsyncClient, user: dict, order_id: int, body: dict):
    return await client.post(
        f"/orders/{order_id}/apply-cancel",
        json=body,
        headers=auth_header(user),
    )


# ---------------------------------------------------------------------------
# 门槛校验
# ---------------------------------------------------------------------------


async def test_apply_cancel_rejects_non_publisher(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
):
    """非发单员本人不能申请取消（403）。"""
    publisher = await _register(
        client, make_captcha, "publisher_a@example.com", "PublisherA"
    )
    other = await _register(client, make_captcha, "other_a@example.com", "OtherA")
    await _fund(client, admin_user, publisher, 200)
    order = await _publish(client, publisher, price="100.00")
    await _accept(client, booster_user, order["id"])

    resp = await _apply_cancel(
        client, other, order["id"], {"reason": "我不想打了", "deduction_amount": 0}
    )
    assert resp.status_code == 403, resp.text
    assert "只有订单发单人可以申请取消" in resp.json()["detail"]

    # 订单不受影响
    assert (await _db_order(db_session, order["id"])).status == OrderStatus.LOCKED


async def test_apply_cancel_rejects_non_locked_order(
    client: AsyncClient, admin_user: dict, make_captcha
):
    """待接单（PENDING）订单不能走申请取消，提示直接取消。"""
    publisher = await _register(
        client, make_captcha, "publisher_b@example.com", "PublisherB"
    )
    await _fund(client, admin_user, publisher, 200)
    order = await _publish(client, publisher, price="100.00")

    resp = await _apply_cancel(
        client, publisher, order["id"], {"reason": "我不想打了", "deduction_amount": 0}
    )
    assert resp.status_code == 400, resp.text
    assert "仅进行中的订单可申请取消" in resp.json()["detail"]


async def test_apply_cancel_rejects_blank_reason(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
):
    """全空白原因能穿过 schema 的 min_length，但被 service 拦下（400）。"""
    publisher = await _register(
        client, make_captcha, "publisher_c@example.com", "PublisherC"
    )
    await _fund(client, admin_user, publisher, 200)
    order = await _publish(client, publisher, price="100.00")
    await _accept(client, booster_user, order["id"])

    resp = await _apply_cancel(
        client, publisher, order["id"], {"reason": "   ", "deduction_amount": 0}
    )
    assert resp.status_code == 400, resp.text
    assert "取消原因至少 3 个字" in resp.json()["detail"]

    # 未发生任何变更
    assert (await _db_order(db_session, order["id"])).status == OrderStatus.LOCKED
    claims = await _order_claims(db_session, order["id"])
    assert [c.status for c in claims] == [ClaimLifecycleStatus.CLAIMED]


async def test_apply_cancel_rejects_deduction_above_booster_deposit(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
):
    """扣款超过接单人当前保证金时拒绝，并告知最高可扣金额。"""
    publisher = await _register(
        client, make_captcha, "publisher_d@example.com", "PublisherD"
    )
    await _fund(client, admin_user, publisher, 200)
    await _set_deposit(client, admin_user, booster_user, 50)
    order = await _publish(client, publisher, price="100.00")
    await _accept(client, booster_user, order["id"])

    resp = await _apply_cancel(
        client, publisher, order["id"], {"reason": "节奏对不上", "deduction_amount": 60}
    )
    assert resp.status_code == 400, resp.text
    assert "最高可扣" in resp.json()["detail"]
    assert "50.00" in resp.json()["detail"]

    # 先校验后变更：订单、名额、保证金都没动
    order_row = await _db_order(db_session, order["id"])
    assert order_row.status == OrderStatus.LOCKED
    assert order_row.notes is None
    claims = await _order_claims(db_session, order["id"])
    assert [c.status for c in claims] == [ClaimLifecycleStatus.CLAIMED]
    assert all(c.approved_deduction is None for c in claims)
    wallet = await _wallet(db_session, booster_user["user"]["id"])
    assert wallet.deposit_balance == Decimal("50.00")


async def test_apply_cancel_rejects_deduction_without_active_claim(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """LOCKED 单没有活跃接单人时，扣款请求被拒绝。"""
    publisher = await _register(
        client, make_captcha, "publisher_e@example.com", "PublisherE"
    )
    order = Order(
        user_id=publisher["user"]["id"],
        game_name="王者荣耀",
        current_rank="钻石",
        target_rank="王者",
        price=Decimal("100.00"),
        max_claims=1,
        claimed_count=0,
        claim_status=ClaimStatus.OPEN,
        status=OrderStatus.LOCKED,
    )
    db_session.add(order)
    await db_session.commit()

    resp = await _apply_cancel(
        client, publisher, order.id, {"reason": "打不了了这个", "deduction_amount": 10}
    )
    assert resp.status_code == 400, resp.text
    assert "该订单没有可扣除的接单人" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------


async def test_apply_cancel_success_moves_money_and_writes_claim_fields(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
):
    """成功取消：订单/名额关闭、扣款落库、两条流水金额相等、notes 前置原因。"""
    publisher = await _register(
        client, make_captcha, "publisher_f@example.com", "PublisherF"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher, 200)
    await _set_deposit(client, admin_user, booster_user, 50)
    order = await _publish(client, publisher, price="100.00")
    await _accept(client, booster_user, order["id"])
    reason = "临时有变故，这个单不跑了"

    resp = await _apply_cancel(
        client, publisher, order["id"], {"reason": reason, "deduction_amount": 30}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CANCELLED"

    # 订单与名额状态
    order_row = await _db_order(db_session, order["id"])
    assert order_row.status == OrderStatus.CANCELLED
    assert order_row.notes.startswith(f"取消原因: {reason}")
    assert "扣除接单人保证金 ¥30.00" in order_row.notes
    claims = await _order_claims(db_session, order["id"])
    assert [c.status for c in claims] == [ClaimLifecycleStatus.CANCELLED]
    assert claims[0].approved_deduction == Decimal("30.00")
    assert claims[0].approved_note == reason

    # 打手保证金 -30、发单员可用余额 +30，两条流水金额相等
    deduct_rows = await _transactions(db_session, user_id=booster_id, tx_type=DEDUCT)
    assert len(deduct_rows) == 1
    # 先把标量取出来：后面每次读库 helper 都会 expire_all()，跨 helper 再访问
    # 早先取出的 ORM 对象会触发同步懒刷新，async 会话里直接 MissingGreenlet
    deduct_amount = deduct_rows[0].amount
    assert deduct_amount == Decimal("-30.00")
    assert deduct_rows[0].order_id == order["id"]
    assert deduct_rows[0].booster_id == booster_id

    in_rows = await _transactions(db_session, user_id=publisher_id, tx_type=COMPENSATE_IN)
    assert len(in_rows) == 1
    assert in_rows[0].amount == Decimal("30.00")
    assert in_rows[0].amount == -deduct_amount
    # 取消赔偿不算打手收入
    publisher_wallet = await _wallet(db_session, publisher_id)
    assert publisher_wallet.total_income == Decimal("0.00")

    # 余额账：发单员 200 - 100 托管 + 100 解冻 + 30 赔偿 = 230；打手保证金 50 - 30 = 20
    assert publisher_wallet.available_balance == Decimal("230.00")
    booster_wallet = await _wallet(db_session, booster_id)
    assert booster_wallet.deposit_balance == Decimal("20.00")


async def test_apply_cancel_zero_deduction_has_no_money_flow(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
):
    """默认 0 = 不扣：没有任何取消赔偿流水，approved_deduction 置空。"""
    publisher = await _register(
        client, make_captcha, "publisher_g@example.com", "PublisherG"
    )
    publisher_id = publisher["user"]["id"]
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher, 200)
    await _set_deposit(client, admin_user, booster_user, 50)
    order = await _publish(client, publisher, price="100.00")
    await _accept(client, booster_user, order["id"])

    resp = await _apply_cancel(
        client, publisher, order["id"], {"reason": "改主意了，先不打了"}
    )
    assert resp.status_code == 200, resp.text

    # 0 元扣款：没有任何取消赔偿流水，保证金不动；托管全额解冻回可用余额
    # （与普通取消同一路径，release_all_escrow 把发布时冻结的 100 退回）
    assert await _transactions(db_session, user_id=booster_id, tx_type=DEDUCT) == []
    assert (
        await _transactions(db_session, user_id=publisher_id, tx_type=COMPENSATE_IN)
        == []
    )
    assert (await _wallet(db_session, booster_id)).deposit_balance == Decimal("50.00")
    # 200 充值 - 100 托管 + 100 解冻 = 200；没有那笔 +30 的赔偿入账
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal(
        "200.00"
    )

    claims = await _order_claims(db_session, order["id"])
    assert [c.status for c in claims] == [ClaimLifecycleStatus.CANCELLED]
    assert claims[0].approved_deduction is None
    assert claims[0].approved_note == "改主意了，先不打了"
    # notes 只有原因，不带扣款段
    order_row = await _db_order(db_session, order["id"])
    assert order_row.notes.startswith("取消原因: 改主意了，先不打了")
    assert "扣除接单人保证金" not in order_row.notes


async def test_apply_cancel_multi_claim_deducts_every_active_booster(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
):
    """多接单：按活跃接单人数逐人扣，发单员入账等额放大。"""
    publisher = await _register(
        client, make_captcha, "publisher_h@example.com", "PublisherH"
    )
    publisher_id = publisher["user"]["id"]
    second = await _register(
        client, make_captcha, "booster_h2@example.com", "BoosterH2"
    )
    await _promote_booster(db_session, "booster_h2@example.com")
    booster_ids = [booster_user["user"]["id"], second["user"]["id"]]

    await _fund(client, admin_user, publisher, 200)
    for booster in (booster_user, second):
        await _set_deposit(client, admin_user, booster, 40)

    order = await _publish(client, publisher, price="50.00", max_claims=2)
    await _accept(client, booster_user, order["id"])
    await _accept(client, second, order["id"])

    resp = await _apply_cancel(
        client, publisher, order["id"], {"reason": "号主退游了", "deduction_amount": 25}
    )
    assert resp.status_code == 200, resp.text

    # 每个活跃接单人各扣 25，两个名额都关闭并记下扣款
    for booster_id in booster_ids:
        rows = await _transactions(db_session, user_id=booster_id, tx_type=DEDUCT)
        assert len(rows) == 1, f"booster {booster_id} should have one deduction row"
        assert rows[0].amount == Decimal("-25.00")
        assert rows[0].booster_id == booster_id
        assert (await _wallet(db_session, booster_id)).deposit_balance == Decimal(
            "15.00"
        )

    claims = await _order_claims(db_session, order["id"])
    assert len(claims) == 2
    assert [c.status for c in claims] == [
        ClaimLifecycleStatus.CANCELLED,
        ClaimLifecycleStatus.CANCELLED,
    ]
    assert all(c.approved_deduction == Decimal("25.00") for c in claims)
    assert all(c.approved_note == "号主退游了" for c in claims)

    # 发单员入账 = 25 × 2 人；200 - 100 托管 + 100 解冻 + 50 赔偿 = 250
    in_rows = await _transactions(db_session, user_id=publisher_id, tx_type=COMPENSATE_IN)
    assert len(in_rows) == 1
    assert in_rows[0].amount == Decimal("50.00")
    assert (await _wallet(db_session, publisher_id)).available_balance == Decimal(
        "250.00"
    )


async def test_apply_cancel_notifies_active_boosters(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
):
    """取消成功后给每个活跃接单人落一条取消通知（含原因与扣款金额）。"""
    publisher = await _register(
        client, make_captcha, "publisher_i@example.com", "PublisherI"
    )
    booster_id = booster_user["user"]["id"]
    await _fund(client, admin_user, publisher, 200)
    await _set_deposit(client, admin_user, booster_user, 50)
    order = await _publish(client, publisher, price="100.00")
    await _accept(client, booster_user, order["id"])
    reason = "我这边人手排不开了"

    resp = await _apply_cancel(
        client, publisher, order["id"], {"reason": reason, "deduction_amount": 30}
    )
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    result = await db_session.execute(
        select(Notification).where(
            Notification.user_id == booster_id,
            Notification.type == NotificationType.ORDER_CANCELLED,
            Notification.ref_id == order["id"],
        )
    )
    notifications = list(result.scalars().all())
    assert len(notifications) == 1
    assert notifications[0].title == "订单已被发单员取消"
    assert reason in notifications[0].content
    assert "30.00" in notifications[0].content
