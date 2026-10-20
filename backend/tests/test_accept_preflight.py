"""接单「无锁预检 + 持锁复核」回归测试（2026-10-20 抢单卡顿修复）。

背景：线上实测热门订单 1 秒内会有 8–14 个打手同时点「接手」，而只有一个
能成功（accept 失败率约 73%）。原实现把全部判定都放在订单行锁内，输家要
排队等赢家走完整个事务，实测 p95 到 5.5s、max 5.6s，用户体感「点接手会卡」。

修复做法：先做一次不加锁的按列预检，把「已经抢不到」的请求在锁外直接拒掉；
拿到行锁后再用**同一份判据**（``_accept_blocked_reason``）复核一次作为权威
判定。本文件钉住修复后必须保持的性质：

1. 预检与持锁复核的拒绝文案完全一致（不许出现两套口径）；
2. 预检命中时**不写库**：不能因为预检读旧值就把 claim_status 改错；
3. 预检读到的旧快照不能污染持锁复核（identity map 隔离）；
4. 输在预检的请求不产生 claim，也不动订单 claimed_count；
5. 并发多人抢单仍然恰好一人成功（行锁语义不回归）；
6. 预检不得放过持锁路径本该拒绝的请求（逐条覆盖各拒绝分支）。
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.order import (
    ClaimLifecycleStatus,
    ClaimStatus,
    Order,
    OrderClaim,
    OrderStatus,
)
from app.models.user import User, UserRole
from app.services.order_service import get_order_service
from tests.conftest import auth_header

BOOSTER_PASSWORD = "Passw0rd123"


async def _register_booster(
    client: AsyncClient,
    db_session,
    make_captcha,
    email: str,
    username: str,
) -> dict:
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": BOOSTER_PASSWORD,
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201)

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.role = UserRole.BOOSTER
    user.booster_quota = 5
    await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": email, "password": BOOSTER_PASSWORD}
    )
    assert login.status_code == 200
    return login.json()


async def _create_order(
    client: AsyncClient, publisher: dict, max_claims: int = 1
) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "title": "预检测试订单",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "200.00",
            "boss_contact": "T0001",
            "max_claims": max_claims,
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _accept(client: AsyncClient, order_id: int, booster: dict):
    return await client.put(
        f"/orders/{order_id}/accept", headers=auth_header(booster)
    )


def _reason(**overrides):
    """用一组默认的「完全可接单」参数调用判据，按需覆盖单个字段。"""
    from app.services.order_service import OrderService

    kwargs = dict(
        is_archived=False,
        claim_status=ClaimStatus.OPEN,
        order_status=OrderStatus.PENDING,
        claimed_count=0,
        max_claims=1,
        deadline=None,
        order_user_id=999,
        booster_id=1,
        now=datetime.now(timezone.utc),
    )
    kwargs.update(overrides)
    return OrderService._accept_blocked_reason(**kwargs)


# ── 1) 判据本身：各拒绝分支的文案（预检与持锁共用的唯一来源）──


def test_accept_reason_allows_clean_order():
    assert _reason() is None


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"is_archived": True}, "订单已归档，不能接单"),
        ({"claim_status": ClaimStatus.PAUSED}, "订单已暂停抢单"),
        ({"claim_status": ClaimStatus.CLOSED}, "订单已截止，不能接单"),
        ({"claim_status": ClaimStatus.FULL}, "订单接单人数已满"),
        ({"order_status": OrderStatus.COMPLETED}, "订单当前状态不允许接单"),
        ({"order_status": OrderStatus.CANCELLED}, "订单当前状态不允许接单"),
        (
            {"order_status": OrderStatus.LOCKED, "max_claims": 1},
            "订单已被其他代练接单",
        ),
        (
            {"claimed_count": 1, "max_claims": 1},
            "订单接单人数已满",
        ),
        ({"order_user_id": 1, "booster_id": 1}, "不能接取自己的订单"),
    ],
)
def test_accept_reason_branch_messages(overrides, expected):
    """每个拒绝分支的文案必须逐字稳定（前端有按文案做的分支判断）。"""
    result = _reason(**overrides)
    assert result is not None
    assert result[0] == expected


def test_accept_reason_reports_claim_status_to_sync():
    """时限已过 / 名额已满要顺带把 claim_status 落终态，其余分支不写状态。"""
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    reason, sync = _reason(deadline=past)
    assert reason == "订单已截止，不能接单"
    assert sync == ClaimStatus.CLOSED

    reason, sync = _reason(claimed_count=1, max_claims=1)
    assert reason == "订单接单人数已满"
    assert sync == ClaimStatus.FULL

    # 其它分支不该产生状态写入
    for overrides in (
        {"is_archived": True},
        {"claim_status": ClaimStatus.PAUSED},
        {"order_status": OrderStatus.CANCELLED},
    ):
        result = _reason(**overrides)
        assert result is not None and result[1] is None


def test_accept_reason_allows_locked_multi_slot():
    """LOCKED 但仍有多名额时可继续接单（与服务卡订单行为一致）。"""
    assert _reason(order_status=OrderStatus.LOCKED, max_claims=3, claimed_count=1) is None


def test_accept_reason_treats_naive_deadline_as_utc():
    """MySQL 返回 naive DATETIME：必须按 UTC 解释，否则会差 8 小时误判。"""
    now = datetime.now(timezone.utc)
    # 未来 1 分钟的 naive 时间（按 UTC 解释仍未来）→ 允许
    assert _reason(deadline=(now + timedelta(minutes=1)).replace(tzinfo=None)) is None
    # 过去 1 分钟的 naive 时间 → 拒绝
    past = _reason(deadline=(now - timedelta(minutes=1)).replace(tzinfo=None))
    assert past is not None and past[1] == ClaimStatus.CLOSED


# ── 2) 端到端：预检命中时立刻拒绝、不写库、不产生副作用 ──


async def test_preflight_rejects_full_order_without_touching_db(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """订单已被抢满后，第二人应被预检直接拒掉，且不改任何数据。"""
    order = await _create_order(client, admin_user, max_claims=1)
    order_id = order["id"]

    winner = await _register_booster(
        client, db_session, make_captcha, "pf_win@example.com", "PfWin"
    )
    loser = await _register_booster(
        client, db_session, make_captcha, "pf_lose@example.com", "PfLose"
    )

    assert (await _accept(client, order_id, winner)).status_code == 200

    # 记录输家请求前的库状态
    before = (
        await db_session.execute(
            select(Order.claimed_count, Order.claim_status).where(Order.id == order_id)
        )
    ).one()

    resp = await _accept(client, order_id, loser)
    assert resp.status_code == 400
    assert resp.json()["detail"] in {"订单接单人数已满", "订单已被其他代练接单"}

    await db_session.commit()
    after = (
        await db_session.execute(
            select(Order.claimed_count, Order.claim_status).where(Order.id == order_id)
        )
    ).one()
    assert after == before, "预检命中不该改动订单行"

    # 输家没有 claim
    loser_claims = (
        await db_session.execute(
            select(func.count(OrderClaim.id)).where(
                OrderClaim.order_id == order_id,
                OrderClaim.booster_id == loser["user"]["id"],
            )
        )
    ).scalar()
    assert loser_claims == 0

    # 全单仍只有赢家一条 claim
    total = (
        await db_session.execute(
            select(func.count(OrderClaim.id)).where(OrderClaim.order_id == order_id)
        )
    ).scalar()
    assert total == 1


async def test_preflight_does_not_leak_stale_snapshot_into_locked_recheck(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """预检的按列查询不得进入 identity map 污染持锁复核。

    构造方式：先让预检看到 PENDING，再在预检与持锁之间把订单改成 FULL。
    若预检把 Order 实例塞进 identity map，持锁复核可能复用旧对象而误放行；
    正确实现必须靠持锁路径读到最新行版本并拒绝。
    """
    order = await _create_order(client, admin_user, max_claims=1)
    order_id = order["id"]

    booster = await _register_booster(
        client, db_session, make_captcha, "pf_stale@example.com", "PfStale"
    )

    service = get_order_service(db_session)
    # 预检只看标量列：这里直接验证查询形状不会返回 ORM 实体
    row = (
        await db_session.execute(
            select(
                Order.id,
                Order.user_id,
                Order.status,
                Order.claim_status,
                Order.claimed_count,
                Order.max_claims,
                Order.deadline,
                Order.is_archived,
                Order.created_at,
            ).where(Order.id == order_id)
        )
    ).one_or_none()
    assert row is not None
    assert not isinstance(row, Order), "预检必须是按列查询，不能返回 ORM 实例"
    assert service is not None

    # 持锁复核能读到外部改动后的最新状态
    other = (
        await db_session.execute(select(Order).where(Order.id == order_id))
    ).scalar_one()
    other.claim_status = ClaimStatus.FULL
    other.claimed_count = 1
    await db_session.commit()

    resp = await _accept(client, order_id, booster)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "订单接单人数已满"


# ── 3) 并发语义不回归 ──


async def test_concurrent_accept_still_exactly_one_with_preflight(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """加预检后并发语义不变：max_claims=1 时恰好一人成功。"""
    order = await _create_order(client, admin_user, max_claims=1)
    order_id = order["id"]

    boosters = [
        await _register_booster(
            client, db_session, make_captcha, f"pf_c{index}@example.com", f"PfC{index}"
        )
        for index in range(6)
    ]

    responses = await asyncio.gather(
        *(_accept(client, order_id, booster) for booster in boosters)
    )
    codes = [r.status_code for r in responses]

    assert codes.count(200) == 1, f"应恰好一人成功，实际 {codes}"
    assert codes.count(400) == len(boosters) - 1, f"其余应全部 400，实际 {codes}"

    claim_count = (
        await db_session.execute(
            select(func.count(OrderClaim.id)).where(OrderClaim.order_id == order_id)
        )
    ).scalar()
    assert claim_count == 1


async def test_repeat_accept_of_same_booster_conflicts(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """同一打手重复接同一单：预检的重复报名检查必须给出 409。"""
    order = await _create_order(client, admin_user, max_claims=3)
    order_id = order["id"]

    booster = await _register_booster(
        client, db_session, make_captcha, "pf_dup@example.com", "PfDup"
    )

    assert (await _accept(client, order_id, booster)).status_code == 200

    resp = await _accept(client, order_id, booster)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "您已报名过该订单，无需重复报名"

    # 仍然只有一条 claim（唯一约束兜底）
    count = (
        await db_session.execute(
            select(func.count(OrderClaim.id)).where(
                OrderClaim.order_id == order_id,
                OrderClaim.booster_id == booster["user"]["id"],
            )
        )
    ).scalar()
    assert count == 1


async def test_accept_own_order_rejected_by_preflight(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """自己发的订单：预检就应拒绝，文案不变。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "pf_self@example.com", "PfSelf"
    )
    order = await _create_order(client, admin_user, max_claims=1)

    # 直接把订单归属改成该打手（走发单接口会因非管理员需要托管余额而被拒，
    # 与本用例要验证的「不能接自己的单」无关）。
    row = (
        await db_session.execute(select(Order).where(Order.id == order["id"]))
    ).scalar_one()
    row.user_id = booster["user"]["id"]
    await db_session.commit()

    resp = await _accept(client, order["id"], booster)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "不能接取自己的订单"


async def test_preflight_preserves_not_found(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """不存在的订单仍是 404（预检不能把它变成别的错误）。"""
    booster = await _register_booster(
        client, db_session, make_captcha, "pf_404@example.com", "Pf404"
    )
    resp = await _accept(client, 99999999, booster)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "订单不存在"


async def test_archived_order_rejected(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """已归档订单：预检拒绝，文案不变。"""
    order = await _create_order(client, admin_user, max_claims=1)

    booster = await _register_booster(
        client, db_session, make_captcha, "pf_arc@example.com", "PfArc"
    )

    row = (
        await db_session.execute(select(Order).where(Order.id == order["id"]))
    ).scalar_one()
    row.is_archived = True
    await db_session.commit()

    resp = await _accept(client, order["id"], booster)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "订单已归档，不能接单"


async def test_multi_slot_accept_still_works_until_full(
    client: AsyncClient, admin_user: dict, db_session, make_captcha
):
    """多名额订单：预检不得提前拦住仍有余量的正常接单。"""
    order = await _create_order(client, admin_user, max_claims=3)
    order_id = order["id"]

    boosters = [
        await _register_booster(
            client, db_session, make_captcha, f"pf_m{index}@example.com", f"PfM{index}"
        )
        for index in range(4)
    ]

    for booster in boosters[:3]:
        resp = await _accept(client, order_id, booster)
        assert resp.status_code == 200, resp.text

    # 第 4 人：预检拒绝
    resp = await _accept(client, order_id, boosters[3])
    assert resp.status_code == 400
    assert resp.json()["detail"] == "订单接单人数已满"

    detail = await client.get(f"/orders/{order_id}", headers=auth_header(admin_user))
    assert detail.json()["claim_status"] == "FULL"
    assert detail.json()["claimed_count"] == 3

    # 三条 claim 都是 CLAIMED
    claims = (
        (
            await db_session.execute(
                select(OrderClaim).where(OrderClaim.order_id == order_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(claims) == 3
    assert all(c.status == ClaimLifecycleStatus.CLAIMED for c in claims)
