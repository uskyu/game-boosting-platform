"""大厅顶部统计条的全量口径测试（老板 2026-10-19「进行中 19 不准」）。

背景：统计条以前是前端在**当前页列表**上 filter 出来的：

    orders.value.filter(item => item.status === 'LOCKED').length

两个问题（都是老板截图里报的）：

1. **口径错**：交付后等老板确认的名额，订单状态仍是 LOCKED，于是被算进
   「进行中」——打手明明已经交完了，页面还显示他有一堆活在手上。
2. **数量错**：只在当页 20 条上数，翻页、筛选、silent 刷新都会让数字跳变，
   与真实总量对不上（老板看到「19」但实际不是 19）。

现在数字由服务端按全量口径算好，随 /orders/hall-digest 下发：

- 待接单 pending_count：还能抢的订单（PENDING + 有空位 + 未截止 + 未归档）
- 进行中 active_count：已接单但**尚未交付**的名额（CLAIMED 且未结算）
- 待审核 pending_review_count：已交付等确认的名额（DELIVERED 且未结算）
- 今日完成 completed_today：今天（北京时间自然日）结算的名额数

口径一律按「名额（claim）」而不是「订单状态」，这才是打手眼里的真实含义。
"""

from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import select

from app.models.order import ClaimLifecycleStatus, OrderClaim
from tests.conftest import auth_header

DIGEST = "/orders/hall-digest"


async def _digest(client: AsyncClient, user: dict) -> dict:
    resp = await client.get(DIGEST, headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _make_order(client: AsyncClient, user: dict, **overrides) -> dict:
    body = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": "100.00",
        **overrides,
    }
    resp = await client.post("/orders/create", json=body, headers=auth_header(user))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _fund(client: AsyncClient, admin_user: dict, user: dict, amount: float = 500.0):
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "hall stats test"},
        headers=auth_header(admin_user),
    )
    assert resp.status_code in (200, 201), resp.text


async def _fund_and_publish(client: AsyncClient, admin_user: dict, user: dict, **overrides):
    """普通用户发单需要托管余额，先充值再发布。"""
    await _fund(client, admin_user, user)
    return await _make_order(client, user, **overrides)


async def _claim(client: AsyncClient, order_id: int, user: dict) -> dict:
    resp = await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(user)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _deliver(client: AsyncClient, order_id: int, user: dict) -> None:
    """打手提交交付：claim 从 CLAIMED 变成 DELIVERED（等老板确认）。

    注意端点是 PUT /orders/{id}/deliver（不是 POST）。
    """
    resp = await client.put(
        f"/orders/{order_id}/deliver",
        json={"delivery_note": "已按要求完成，请确认"},
        headers=auth_header(user),
    )
    assert resp.status_code in (200, 201), resp.text


# ---------------------------------------------------------------------------
# 待接单
# ---------------------------------------------------------------------------


async def test_pending_count_tracks_claimable_orders(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    before = await _digest(client, booster_user)
    assert before["pending_count"] == 0

    await _make_order(client, admin_user)

    after = await _digest(client, booster_user)
    assert after["pending_count"] == 1


async def test_pending_count_drops_when_order_filled(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """订单被接满后不再是「待接单」。"""
    order = await _make_order(client, admin_user, max_claims=1)
    assert (await _digest(client, booster_user))["pending_count"] == 1

    await _claim(client, order["id"], booster_user)

    assert (await _digest(client, booster_user))["pending_count"] == 0


# ---------------------------------------------------------------------------
# 进行中 / 待审核：核心口径修正
# ---------------------------------------------------------------------------


async def test_delivered_claim_moves_out_of_active_into_review(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """老板报的核心问题：交付后等确认的名额不能再算「进行中」。

    交付前：进行中 1 / 待审核 0
    交付后：进行中 0 / 待审核 1
    """
    order = await _make_order(client, admin_user, max_claims=1)
    await _claim(client, order["id"], booster_user)

    in_progress = await _digest(client, booster_user)
    assert in_progress["active_count"] == 1
    assert in_progress["pending_review_count"] == 0

    await _deliver(client, order["id"], booster_user)

    after_delivery = await _digest(client, booster_user)
    assert after_delivery["active_count"] == 0, "已交付的名额不该再算进行中"
    assert after_delivery["pending_review_count"] == 1


async def test_active_count_counts_claims_not_order_status(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """多名额订单：管理员按**名额数**计，不是按订单条数计。

    一张 2 名额订单被两个人各接一个名额，管理员看到 2（而不是 1 条订单）。
    注意打手自己看到的仍是**他自己那 1 个**名额（口径见下方
    test_booster_active_count_excludes_other_boosters_claims）。
    """
    order = await _make_order(client, admin_user, max_claims=2, price="100.00")
    # 两个不同打手各接一个名额（同一用户不能重复接同一单）
    other_booster = None
    resp = await client.post(
        "/auth/register",
        json={
            "email": "booster2@example.com",
            "username": "TestBooster2",
            "password": "BoostPass123",
            **await _captcha(client),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    other_booster = resp.json()

    await _claim(client, order["id"], booster_user)
    await _claim(client, order["id"], other_booster)

    # 管理员看全平台名额：2
    assert (await _digest(client, admin_user))["active_count"] == 2
    # 打手只看自己的：1
    assert (await _digest(client, booster_user))["active_count"] == 1


async def test_review_count_tracks_delivered_claims(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    order = await _make_order(client, admin_user, max_claims=1)
    await _claim(client, order["id"], booster_user)
    await _deliver(client, order["id"], booster_user)

    digest = await _digest(client, booster_user)
    assert digest["pending_review_count"] == 1
    assert digest["active_count"] == 0


# ---------------------------------------------------------------------------
# 今日完成
# ---------------------------------------------------------------------------


async def test_completed_today_counts_settled_claims_only(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
):
    """今日完成只看「今天结算的名额」：未结算的、昨天的都不算。"""
    assert (await _digest(client, booster_user))["completed_today"] == 0

    order = await _make_order(client, admin_user, max_claims=1)
    await _claim(client, order["id"], booster_user)
    await _deliver(client, order["id"], booster_user)

    # 交付但未结算 → 不算完成
    assert (await _digest(client, booster_user))["completed_today"] == 0

    # 人为把该名额标记为「今天已结算」
    result = await db_session.execute(
        select(OrderClaim).where(
            OrderClaim.order_id == order["id"],
            OrderClaim.booster_id == booster_user["user"]["id"],
        )
    )
    claim = result.scalar_one()
    claim.status = ClaimLifecycleStatus.SETTLED
    claim.settled_at = datetime.now(timezone.utc)
    await db_session.commit()

    assert (await _digest(client, booster_user))["completed_today"] == 1


async def test_completed_today_ignores_previous_days(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
):
    """昨天结算的不算今日完成（按北京时间自然日切分）。"""
    order = await _make_order(client, admin_user, max_claims=1)
    await _claim(client, order["id"], booster_user)

    result = await db_session.execute(
        select(OrderClaim).where(
            OrderClaim.order_id == order["id"],
            OrderClaim.booster_id == booster_user["user"]["id"],
        )
    )
    claim = result.scalar_one()
    claim.status = ClaimLifecycleStatus.SETTLED
    claim.settled_at = datetime.now(timezone.utc) - timedelta(days=2)
    await db_session.commit()

    assert (await _digest(client, booster_user))["completed_today"] == 0


# ---------------------------------------------------------------------------
# 稳定性：数字不随分页/筛选变化
# ---------------------------------------------------------------------------


async def test_stats_are_full_scope_not_current_page(
    client: AsyncClient, admin_user: dict, booster_user: dict
):
    """统计口径与列表分页无关：翻页拿到同一组数字。

    这正是旧实现的问题——数字在当页数组上 filter，一页 20 条就会失真。
    """
    for _ in range(3):
        await _make_order(client, admin_user)

    digest = await _digest(client, booster_user)
    assert digest["pending_count"] == 3

    # 用 page_size=1 拉列表（此时当页只有 1 条），统计仍是全量 3
    resp = await client.get(
        "/orders/", params={"page": 1, "page_size": 1}, headers=auth_header(booster_user)
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1

    again = await _digest(client, booster_user)
    assert again["pending_count"] == 3


async def test_stats_present_for_admin_without_scope(
    client: AsyncClient, admin_user: dict
):
    """管理员不加可见性过滤，统计照常返回（看的是平台盘子）。"""
    await _make_order(client, admin_user)
    digest = await _digest(client, admin_user)
    assert digest["pending_count"] == 1


# ---------------------------------------------------------------------------
# 关键：打手只能看到「自己的」名额，不能被别人的活污染
# ---------------------------------------------------------------------------


async def _second_booster(client: AsyncClient, db_session) -> dict:
    """注册并提升为第二个打手（BOOSTER 角色）。

    用测试的 db_session 改角色：不能用 async_session_factory，那是另一条
    连接，在测试库隔离环境下看不到本用例写入的数据。
    """
    from app.models.user import User, UserRole

    captcha = await _captcha(client)
    resp = await client.post(
        "/auth/register",
        json={
            "email": "booster_b@example.com",
            "username": "TestBoosterB",
            "password": "BoostPass123",
            **captcha,
        },
    )
    assert resp.status_code in (200, 201), resp.text

    await db_session.commit()
    result = await db_session.execute(
        select(User).where(User.email == "booster_b@example.com")
    )
    user = result.scalar_one()
    user.role = UserRole.BOOSTER
    user.booster_quota = 5
    await db_session.commit()

    login = await client.post(
        "/auth/login",
        json={"email": "booster_b@example.com", "password": "BoostPass123"},
    )
    assert login.status_code == 200, login.text
    return login.json()


async def test_booster_active_count_excludes_other_boosters_claims(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """老板 2026-10-19 二次反馈的核心：别人的名额不能算进「我的进行中」。

    两个打手各接一单，A 看到的 active 必须是 1，不是 2。
    """
    booster_b = await _second_booster(client, db_session)

    order_a = await _make_order(client, admin_user, max_claims=1)
    order_b = await _make_order(client, admin_user, max_claims=1)
    await _claim(client, order_a["id"], booster_user)
    await _claim(client, order_b["id"], booster_b)

    digest_a = await _digest(client, booster_user)
    digest_b = await _digest(client, booster_b)

    assert digest_a["active_count"] == 1, (
        f"打手A 只接了自己的 1 单，却看到 {digest_a['active_count']}（别人被算进来了）"
    )
    assert digest_b["active_count"] == 1
    # 管理员看全平台 = 2
    assert (await _digest(client, admin_user))["active_count"] == 2


async def test_booster_review_count_excludes_other_boosters_claims(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """同理：别人交付待审核的名额，不算我的「待审核」。"""
    booster_b = await _second_booster(client, db_session)

    order_a = await _make_order(client, admin_user, max_claims=1)
    order_b = await _make_order(client, admin_user, max_claims=1)
    await _claim(client, order_a["id"], booster_user)
    await _claim(client, order_b["id"], booster_b)
    await _deliver(client, order_a["id"], booster_user)
    await _deliver(client, order_b["id"], booster_b)

    digest_a = await _digest(client, booster_user)
    assert digest_a["pending_review_count"] == 1, (
        f"打手A 只交了 1 单，却看到 {digest_a['pending_review_count']}"
    )
    assert digest_a["active_count"] == 0

    # 管理员看全平台 = 2
    assert (await _digest(client, admin_user))["pending_review_count"] == 2


async def test_booster_completed_today_excludes_other_boosters(
    client: AsyncClient,
    admin_user: dict,
    booster_user: dict,
    db_session,
):
    """老板原话「我一天啥也没干，今日完成也是有」：别人结算的单不能算我的。"""
    booster_b = await _second_booster(client, db_session)

    order_b = await _make_order(client, admin_user, max_claims=1)
    await _claim(client, order_b["id"], booster_b)

    # 把 B 的名额标记为「今天已结算」
    result = await db_session.execute(
        select(OrderClaim).where(
            OrderClaim.order_id == order_b["id"],
            OrderClaim.booster_id == booster_b["user"]["id"],
        )
    )
    claim = result.scalar_one()
    claim.status = ClaimLifecycleStatus.SETTLED
    claim.settled_at = datetime.now(timezone.utc)
    await db_session.commit()

    # A 什么都没干 → 今日完成必须是 0
    assert (await _digest(client, booster_user))["completed_today"] == 0, (
        "打手 A 没干活却显示了今日完成（别人结算的被算进来了）"
    )
    # B 自己看到 1
    assert (await _digest(client, booster_b))["completed_today"] == 1
    # 管理员看全平台 = 1
    assert (await _digest(client, admin_user))["completed_today"] == 1


async def test_pending_count_is_shared_hall_view_for_all_boosters(
    client: AsyncClient, admin_user: dict, booster_user: dict, db_session
):
    """「待接单」是公共大厅口径：所有打手看到同一个数（这点和另外三项不同）。"""
    booster_b = await _second_booster(client, db_session)
    await _make_order(client, admin_user, max_claims=1)
    await _make_order(client, admin_user, max_claims=1)

    assert (await _digest(client, booster_user))["pending_count"] == 2
    assert (await _digest(client, booster_b))["pending_count"] == 2
    assert (await _digest(client, admin_user))["pending_count"] == 2


async def _captcha(client: AsyncClient) -> dict:
    """取一张验证码（注册第二个打手用）。"""
    from app.services import captcha_service

    captcha_id, _ = captcha_service.create()
    code, _ = captcha_service._store[captcha_id]
    return {"captcha_id": captcha_id, "captcha_code": code}
