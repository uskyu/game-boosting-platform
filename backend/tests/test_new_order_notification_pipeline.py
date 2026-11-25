"""新单通知流水线（快照 → post-commit 后台任务）专项测试。

覆盖老板拍板的两个收敛方案：
- 发单请求不再同步写通知，事务提交后在后台任务里落库/推送；
- 公开单只给当前在线且满足既有资格的打手写通知行，离线用户不落库；
  指定单只追 1 个目标，不要求在线（离线也写 1 条）。

通知行断言统一按 ``ref_id == order_id AND type == SYSTEM_ANNOUNCEMENT``。
"""

from sqlalchemy import select

from app.api import notification_utils
from app.models.notification import Notification, NotificationType, UserPreference
from app.models.user import User, UserRole
from tests.conftest import auth_header


class _RecordingManager:
    """可控在线集合 + 记录 WS 扇出的连接管理器替身。"""

    def __init__(self) -> None:
        self.online_ids: set[int] = set()
        self.online_calls = 0
        self.sent: list[dict] = []

    async def online_user_ids(self) -> set[int]:
        self.online_calls += 1
        return set(self.online_ids)

    async def send_notification(self, user_id: int, notification: dict) -> None:
        self.sent.append({"user_id": user_id, "payload": notification})


async def _create_order(client, publisher: dict, **overrides) -> dict:
    body = {
        "game_name": "王者荣耀",
        "current_rank": "钻石",
        "target_rank": "王者",
        "price": "100.00",
        "title": "新单通知流水线测试",
        "boss_contact": "BOSS-001",
        **overrides,
    }
    resp = await client.post(
        "/orders/create", json=body, headers=auth_header(publisher)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _notifications(db_session, order_id: int) -> list[Notification]:
    result = await db_session.execute(
        select(Notification).where(
            Notification.ref_id == order_id,
            Notification.type == NotificationType.SYSTEM_ANNOUNCEMENT,
        )
    )
    return list(result.scalars().all())


async def _run_hooks(hooks) -> None:
    """手动执行被拦截的 post-commit 钩子（模拟事务已提交）。"""
    pending = list(hooks)
    hooks.clear()
    for factory in pending:
        await factory()


def _make_manager(monkeypatch) -> _RecordingManager:
    manager = _RecordingManager()
    monkeypatch.setattr(
        notification_utils, "get_connection_manager", lambda: manager
    )
    return manager


def _capture_hooks(monkeypatch) -> list:
    hooks: list = []

    async def _capture(factory) -> None:
        hooks.append(factory)

    monkeypatch.setattr(notification_utils, "run_after_commit", _capture)
    return hooks


# ---------------------------------------------------------------------------
# 公开单：只写在线收件人
# ---------------------------------------------------------------------------


async def test_public_order_with_no_online_boosters_writes_no_rows(
    client, db_session, admin_user, booster_user, monkeypatch
):
    hooks = _capture_hooks(monkeypatch)
    manager = _make_manager(monkeypatch)
    manager.online_ids = set()

    order = await _create_order(client, admin_user)
    await _run_hooks(hooks)

    assert manager.online_calls == 1
    assert await _notifications(db_session, order["id"]) == []
    assert manager.sent == []


async def test_public_order_writes_only_online_booster(
    client, db_session, admin_user, booster_user, registered_user, monkeypatch
):
    """在线只有 booster：只写 1 行，离线普通用户不落库。"""
    hooks = _capture_hooks(monkeypatch)
    manager = _make_manager(monkeypatch)
    booster_id = booster_user["user"]["id"]
    manager.online_ids = {booster_id}

    order = await _create_order(client, admin_user)
    await _run_hooks(hooks)

    rows = await _notifications(db_session, order["id"])
    assert len(rows) == 1
    assert rows[0].user_id == booster_id
    assert rows[0].title == "新订单发布"
    assert rows[0].link == f"/orders/{order['id']}"
    assert registered_user["user"]["id"] != rows[0].user_id

    assert len(manager.sent) == 1
    assert manager.sent[0]["user_id"] == booster_id
    assert manager.sent[0]["payload"]["ref_id"] == order["id"]


async def test_public_order_with_many_eligible_candidates_but_none_online_writes_nothing(
    client, db_session, admin_user, monkeypatch
):
    """候选很多但在线集合为空：短路返回，绝不按候选人数写行。"""
    candidates = [
        User(
            email=f"offline-candidate-{index}@example.com",
            username=f"OfflineCandidate{index}",
            hashed_password="not-used",
            role=UserRole.BOOSTER,
            is_active=True,
            can_accept=True,
        )
        for index in range(40)
    ]
    db_session.add_all(candidates)
    await db_session.commit()

    hooks = _capture_hooks(monkeypatch)
    manager = _make_manager(monkeypatch)
    manager.online_ids = set()

    order = await _create_order(client, admin_user)
    await _run_hooks(hooks)

    assert manager.online_calls == 1
    assert await _notifications(db_session, order["id"]) == []


async def test_online_user_with_new_order_preference_off_gets_no_row(
    client, db_session, admin_user, booster_user, monkeypatch
):
    booster_id = booster_user["user"]["id"]
    db_session.add(
        UserPreference(
            user_id=booster_id,
            notification_settings={
                NotificationType.SYSTEM_ANNOUNCEMENT.value: False,
            },
        )
    )
    await db_session.commit()

    hooks = _capture_hooks(monkeypatch)
    manager = _make_manager(monkeypatch)
    manager.online_ids = {booster_id}

    order = await _create_order(client, admin_user)
    await _run_hooks(hooks)

    assert await _notifications(db_session, order["id"]) == []
    assert manager.sent == []


# ---------------------------------------------------------------------------
# 指定单：只追目标，且离线也写
# ---------------------------------------------------------------------------


async def test_designated_order_notifies_only_target_and_skips_online_lookup(
    client, db_session, admin_user, booster_user, registered_user, monkeypatch
):
    target_id = booster_user["user"]["id"]
    other_online_id = registered_user["user"]["id"]

    hooks = _capture_hooks(monkeypatch)
    manager = _make_manager(monkeypatch)
    # 即使其他人也在线，也只给指定人写行；指定单不查在线集合。
    manager.online_ids = {target_id, other_online_id}

    order = await _create_order(
        client, admin_user, designated_booster_id=target_id
    )
    await _run_hooks(hooks)

    assert manager.online_calls == 0
    rows = await _notifications(db_session, order["id"])
    assert len(rows) == 1
    assert rows[0].user_id == target_id
    assert other_online_id not in {row.user_id for row in rows}
    assert len(manager.sent) == 1
    assert manager.sent[0]["user_id"] == target_id


async def test_designated_order_writes_row_even_when_target_offline(
    client, db_session, admin_user, booster_user, monkeypatch
):
    """指定人当前没有 WS 连接：仍写 1 条通知，登录后通知中心可见。"""
    target_id = booster_user["user"]["id"]

    hooks = _capture_hooks(monkeypatch)
    manager = _make_manager(monkeypatch)
    manager.online_ids = set()

    order = await _create_order(
        client, admin_user, designated_booster_id=target_id
    )
    await _run_hooks(hooks)

    assert manager.online_calls == 0
    rows = await _notifications(db_session, order["id"])
    assert len(rows) == 1
    assert rows[0].user_id == target_id
