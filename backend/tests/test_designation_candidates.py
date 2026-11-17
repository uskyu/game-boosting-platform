"""指定派单候选人接口：搜索、最近指定、过滤、权限与限流。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select

from app.models.order import Order
from app.models.publisher_blacklist import PublisherBlacklist
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from app.services.publish_permission_service import (
    PUBLISH_BANNED_DETAIL,
    PUBLISH_NOT_GRANTED_DETAIL,
)
from tests.conftest import auth_header

PASSWORD = "DesignationPass1"
CANDIDATE_KEYS = {
    "id",
    "username",
    "avatar_url",
    "deposit_balance",
    "can_accept",
    "selected_times",
}


async def _register(
    client: AsyncClient,
    make_captcha,
    email: str,
    username: str,
) -> dict:
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": PASSWORD,
            **make_captcha(),
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _make_user(
    db_session,
    *,
    username: str,
    email: str | None = None,
    role: UserRole = UserRole.BOOSTER,
    is_active: bool = True,
    can_accept: bool = True,
    avatar_url: str | None = None,
    deposit_balance: str | None = None,
) -> User:
    user = User(
        email=email or f"{username.lower()}@example.com",
        username=username,
        hashed_password="not-used",
        role=role,
        is_active=is_active,
        can_accept=can_accept,
        avatar_url=avatar_url,
    )
    db_session.add(user)
    await db_session.flush()
    if deposit_balance is not None:
        db_session.add(
            Wallet(user_id=user.id, deposit_balance=Decimal(deposit_balance))
        )
        await db_session.flush()
    return user


async def _designate(
    db_session,
    *,
    publisher_id: int,
    booster_id: int,
    created_at: datetime,
) -> None:
    db_session.add(
        Order(
            user_id=publisher_id,
            designated_booster_id=booster_id,
            game_name="王者荣耀",
            current_rank="钻石",
            target_rank="王者",
            price=Decimal("10.00"),
            created_at=created_at,
        )
    )


async def _get_candidates(client: AsyncClient, user: dict, q: str | None = None):
    params = {} if q is None else {"q": q}
    return await client.get(
        "/orders/designation-candidates",
        params=params,
        headers=auth_header(user),
    )


async def _set_user_flags(db_session, user_id: int, **values) -> None:
    result = await db_session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    for field, value in values.items():
        setattr(user, field, value)
    await db_session.commit()


def _as_decimal(value) -> Decimal:
    return Decimal(str(value))


async def test_search_matches_username_and_email_without_email_and_balances(
    client: AsyncClient, db_session, make_captcha
):
    viewer = await _register(
        client, make_captcha, "cand.viewer@example.com", "CandViewer"
    )
    alpha = await _make_user(
        db_session,
        username="AlphaCandidate",
        email="alpha.login@example.com",
        avatar_url="/avatars/alpha.png",
        deposit_balance="123.45",
    )
    beta = await _make_user(
        db_session,
        username="BetaCandidate",
        email="beta.login@example.com",
        can_accept=False,
    )
    await db_session.commit()

    # 昵称命中：响应只带候选字段，不回显邮箱；有钱包返回真实保证金。
    resp = await _get_candidates(client, viewer, "Alpha")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == "Alpha"
    assert body["recent"] == []
    assert len(body["results"]) == 1
    item = body["results"][0]
    assert item["id"] == alpha.id
    assert item["username"] == "AlphaCandidate"
    assert item["avatar_url"] == "/avatars/alpha.png"
    assert _as_decimal(item["deposit_balance"]) == Decimal("123.45")
    assert item["can_accept"] is True
    assert set(item) == CANDIDATE_KEYS
    assert "email" not in item

    # 登录账号命中（邮箱前缀），无钱包按 0；can_accept=False 保留。
    resp = await _get_candidates(client, viewer, "beta.login")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == "beta.login"
    assert body["recent"] == []
    item = body["results"][0]
    assert item["id"] == beta.id
    assert item["can_accept"] is False
    assert _as_decimal(item["deposit_balance"]) == Decimal("0.00")
    assert set(item) == CANDIDATE_KEYS
    assert "email" not in item

    # 搜索词去空白后展示。
    resp = await _get_candidates(client, viewer, "  Alpha  ")
    assert resp.status_code == 200, resp.text
    assert resp.json()["query"] == "Alpha"

    # 去空白后不足 2 个字符：422 固定文案。
    resp = await _get_candidates(client, viewer, " a ")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "请输入至少 2 个字符"

    # q 为空白等价于空：走最近列表，不触发搜索限流。
    resp = await _get_candidates(client, viewer, "   ")
    assert resp.status_code == 200, resp.text
    assert resp.json()["query"] == ""
    assert resp.json()["recent"] == []
    assert resp.json()["results"] == []


async def test_search_excludes_self_admin_inactive_and_blacklisted(
    client: AsyncClient, db_session, make_captcha
):
    viewer = await _register(
        client, make_captcha, "cand.filter.viewer@example.com", "CandViewer"
    )
    eligible = await _make_user(db_session, username="CandEligible")
    stopped = await _make_user(db_session, username="CandStopped", can_accept=False)
    admin = await _make_user(
        db_session, username="CandAdmin", role=UserRole.ADMIN
    )
    inactive = await _make_user(db_session, username="CandInactive", is_active=False)
    blocked = await _make_user(db_session, username="CandBlocked")
    db_session.add(
        PublisherBlacklist(publisher_id=viewer["user"]["id"], blocked_user_id=blocked.id)
    )
    await db_session.commit()

    resp = await _get_candidates(client, viewer, "Cand")
    assert resp.status_code == 200, resp.text
    items = resp.json()["results"]
    ids = [item["id"] for item in items]

    assert {eligible.id, stopped.id} == set(ids)
    assert len(ids) == 2
    assert viewer["user"]["id"] not in ids
    assert admin.id not in ids
    assert inactive.id not in ids
    assert blocked.id not in ids
    stopped_item = next(item for item in items if item["id"] == stopped.id)
    assert stopped_item["can_accept"] is False


async def test_search_ranks_exact_then_prefix_then_nickname_order(
    client: AsyncClient, db_session, make_captcha
):
    viewer = await _register(
        client, make_captcha, "cand.rank.viewer@example.com", "RankViewer"
    )
    exact = await _make_user(db_session, username="Star", email="exact.star@example.com")
    prefix_first = await _make_user(
        db_session, username="Starfire", email="starfire@example.com"
    )
    prefix_second = await _make_user(
        db_session, username="StarLord", email="starlord@example.com"
    )
    contains = await _make_user(
        db_session, username="NorthStar", email="northstar@example.com"
    )
    await db_session.commit()

    resp = await _get_candidates(client, viewer, "Star")
    assert resp.status_code == 200, resp.text
    ids = [item["id"] for item in resp.json()["results"]]
    assert ids == [exact.id, prefix_first.id, prefix_second.id, contains.id]


async def test_recent_designations_order_by_count_then_latest_and_limit_six(
    client: AsyncClient, db_session, make_captcha
):
    viewer = await _register(
        client, make_captcha, "cand.recent.viewer@example.com", "RecentViewer"
    )
    candidates = [
        await _make_user(db_session, username=f"Recent{index}")
        for index in range(7)
    ]
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    # A=3 次；B=2 次且最近 -2h；C=2 次且最近 -3h；D~G 各 1 次，按最近时间排序。
    for offset in (timedelta(days=10), timedelta(days=5), timedelta(hours=1)):
        await _designate(
            db_session,
            publisher_id=viewer["user"]["id"],
            booster_id=candidates[0].id,
            created_at=now - offset,
        )
    for offset in (timedelta(days=20), timedelta(hours=2)):
        await _designate(
            db_session,
            publisher_id=viewer["user"]["id"],
            booster_id=candidates[1].id,
            created_at=now - offset,
        )
    for offset in (timedelta(days=30), timedelta(hours=3)):
        await _designate(
            db_session,
            publisher_id=viewer["user"]["id"],
            booster_id=candidates[2].id,
            created_at=now - offset,
        )
    for index, offset in enumerate(
        (
            timedelta(minutes=10),
            timedelta(minutes=40),
            timedelta(minutes=50),
            timedelta(minutes=60),
        ),
        start=3,
    ):
        await _designate(
            db_session,
            publisher_id=viewer["user"]["id"],
            booster_id=candidates[index].id,
            created_at=now - offset,
        )
    await db_session.commit()

    resp = await _get_candidates(client, viewer)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == ""
    assert body["results"] == []
    assert [item["id"] for item in body["recent"]] == [
        candidate.id for candidate in candidates[:6]
    ]
    assert [item["selected_times"] for item in body["recent"]] == [3, 2, 2, 1, 1, 1]
    assert all(set(item) == CANDIDATE_KEYS for item in body["recent"])

    # q 非空时 recent 为空数组，由前端继续使用缓存的最近列表。
    resp = await _get_candidates(client, viewer, "Recent")
    assert resp.status_code == 200, resp.text
    assert resp.json()["recent"] == []
    assert len(resp.json()["results"]) == 7


async def test_recent_designations_exclude_self_admin_inactive_blacklisted_and_stale(
    client: AsyncClient, db_session, make_captcha
):
    viewer = await _register(
        client, make_captcha, "cand.recent.filter@example.com", "RecentFilterViewer"
    )
    eligible = await _make_user(db_session, username="FilterEligible")
    stopped = await _make_user(
        db_session, username="FilterStopped", can_accept=False
    )
    admin = await _make_user(db_session, username="FilterAdmin", role=UserRole.ADMIN)
    inactive = await _make_user(
        db_session, username="FilterInactive", is_active=False
    )
    blocked = await _make_user(db_session, username="FilterBlocked")
    stale = await _make_user(db_session, username="FilterStale")
    db_session.add(
        PublisherBlacklist(publisher_id=viewer["user"]["id"], blocked_user_id=blocked.id)
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    for _ in range(2):
        await _designate(
            db_session,
            publisher_id=viewer["user"]["id"],
            booster_id=eligible.id,
            created_at=now - timedelta(hours=1),
        )
    await _designate(
        db_session,
        publisher_id=viewer["user"]["id"],
        booster_id=stopped.id,
        created_at=now - timedelta(hours=2),
    )
    for booster in (admin, inactive, blocked):
        for offset in (timedelta(hours=3), timedelta(hours=4), timedelta(hours=5)):
            await _designate(
                db_session,
                publisher_id=viewer["user"]["id"],
                booster_id=booster.id,
                created_at=now - offset,
            )
    # 自己指定自己：不应出现在候选里。
    for offset in (timedelta(hours=1), timedelta(hours=2), timedelta(hours=3)):
        await _designate(
            db_session,
            publisher_id=viewer["user"]["id"],
            booster_id=viewer["user"]["id"],
            created_at=now - offset,
        )
    # 90 天窗口外的历史指定不计入最近列表。
    for offset in (
        timedelta(days=100),
        timedelta(days=110),
        timedelta(days=120),
        timedelta(days=130),
        timedelta(days=140),
    ):
        await _designate(
            db_session,
            publisher_id=viewer["user"]["id"],
            booster_id=stale.id,
            created_at=now - offset,
        )
    await db_session.commit()

    resp = await _get_candidates(client, viewer)
    assert resp.status_code == 200, resp.text
    recent = resp.json()["recent"]
    assert [item["id"] for item in recent] == [eligible.id, stopped.id]
    assert [item["selected_times"] for item in recent] == [2, 1]
    assert recent[1]["can_accept"] is False
    for filtered_id in (admin.id, inactive.id, blocked.id, stale.id, viewer["user"]["id"]):
        assert filtered_id not in {item["id"] for item in recent}


async def test_designation_candidates_requires_login_and_publish_permission(
    client: AsyncClient, db_session, make_captcha
):
    viewer = await _register(
        client, make_captcha, "cand.permission@example.com", "PermissionViewer"
    )

    resp = await client.get("/orders/designation-candidates")
    assert resp.status_code == 401

    await _set_user_flags(db_session, viewer["user"]["id"], can_publish=False)
    resp = await _get_candidates(client, viewer)
    assert resp.status_code == 403
    assert resp.json()["detail"] == PUBLISH_NOT_GRANTED_DETAIL

    await _set_user_flags(
        db_session, viewer["user"]["id"], can_publish=False, publish_banned=True
    )
    resp = await _get_candidates(client, viewer)
    assert resp.status_code == 403
    assert resp.json()["detail"] == PUBLISH_BANNED_DETAIL


async def test_search_rate_limit_only_applies_to_keyword_queries(
    client: AsyncClient, db_session, make_captcha
):
    viewer = await _register(
        client, make_captcha, "cand.rate@example.com", "RateViewer"
    )

    for _ in range(20):
        resp = await _get_candidates(client, viewer, "zz")
        assert resp.status_code == 200, resp.text

    resp = await _get_candidates(client, viewer, "zz")
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail == {
        "code": "designation_search_rate_limited",
        "message": "查找过于频繁，请稍候再试",
        "retry_after": 60,
    }

    # 最近列表不消耗、也不受搜索额度影响。
    resp = await _get_candidates(client, viewer)
    assert resp.status_code == 200, resp.text
    assert resp.json()["recent"] == []
    assert resp.json()["results"] == []
