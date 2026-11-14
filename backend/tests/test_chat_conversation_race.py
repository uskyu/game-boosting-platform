"""订单聊天会话唯一键冲突与并发建联回归。

线上 CN1 2026-11-13 的日志显示：``POST /api/v1/chat/conversations`` 在
``uq_conversations_order_id`` 唯一键冲突后执行整事务回滚，随后读取已过期
ORM 用户属性触发 ``MissingGreenlet``，最终返回 500。本文件钉住三件事：

1. 同一对用户并发双向建联最终拿到同一个会话，且库里只有一条会话、
   一组参与者和一张订单卡片系统消息；
2. 唯一键冲突后的恢复路径不再依赖过期 ORM 对象，能返回并发赢家会话；
3. 订单已有另一组参与者的会话时，后来者只能得到 409，不能复用或读取
   别人的会话，也不会被自动加入。
"""

import asyncio

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.chat import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageType,
)
from app.models.user import User, UserRole
from app.services.chat_service import ChatService
from tests.conftest import _session_factory, auth_header

BOOSTER_PASSWORD = "BoostPass123"


async def _register_booster(
    client: AsyncClient,
    db_session,
    make_captcha,
    *,
    email: str,
    username: str,
) -> dict:
    """注册并提升为打手，返回重新登录后的令牌载荷。"""
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

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()
    user.role = UserRole.BOOSTER
    user.booster_quota = 5
    await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": email, "password": BOOSTER_PASSWORD}
    )
    assert login.status_code == 200, login.text
    return login.json()


async def _top_up(client: AsyncClient, admin: dict, user: dict, amount: int = 500) -> None:
    resp = await client.post(
        f"/admin/wallets/{user['user']['id']}/adjust",
        json={"amount": amount, "reason": "test chat conversation race"},
        headers=auth_header(admin),
    )
    assert resp.status_code in (200, 201), resp.text


async def _create_order(client: AsyncClient, publisher: dict) -> dict:
    resp = await client.post(
        "/orders/create",
        json={
            "game_name": "王者荣耀",
            "current_rank": "钻石",
            "target_rank": "王者",
            "price": "100.00",
        },
        headers=auth_header(publisher),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _open_conversation(
    client: AsyncClient,
    user: dict,
    *,
    target_user_id: int,
    order_id: int,
):
    return await client.post(
        "/chat/conversations",
        headers=auth_header(user),
        json={"target_user_id": target_user_id, "order_id": order_id},
    )


async def test_concurrent_order_conversation_requests_return_same_conversation(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
) -> None:
    """发布者与打手同时双向建联：两个请求都成功且返回同一会话。"""
    publisher = registered_user
    booster = booster_user
    await _top_up(client, admin_user, publisher)
    order = await _create_order(client, publisher)
    order_id = order["id"]

    resp_owner, resp_booster = await asyncio.gather(
        _open_conversation(
            client,
            publisher,
            target_user_id=booster["user"]["id"],
            order_id=order_id,
        ),
        _open_conversation(
            client,
            booster,
            target_user_id=publisher["user"]["id"],
            order_id=order_id,
        ),
    )
    assert resp_owner.status_code == 200, resp_owner.text
    assert resp_booster.status_code == 200, resp_booster.text
    assert resp_owner.json()["id"] == resp_booster.json()["id"]

    conversation_id = resp_owner.json()["id"]

    # 库里只有一条订单会话、一组双方参与者、一张订单卡片系统消息。
    conversation_count = (
        await db_session.execute(
            select(func.count(Conversation.id)).where(Conversation.order_id == order_id)
        )
    ).scalar()
    assert conversation_count == 1

    participant_ids = set(
        (
            await db_session.execute(
                select(ConversationParticipant.user_id).where(
                    ConversationParticipant.conversation_id == conversation_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert participant_ids == {publisher["user"]["id"], booster["user"]["id"]}

    system_message_count = (
        await db_session.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation_id,
                Message.message_type == MessageType.SYSTEM,
            )
        )
    ).scalar()
    assert system_message_count == 1


class _ConflictInjectingSession:
    """在服务第一次给用户行加锁前，用另一事务提交同订单会话。

    模拟线上最坏时序：并发赢家在本请求建立一致性读快照之后才提交，
    本请求在后续普通 SELECT 里看不到它，只能撞唯一键再走恢复。
    """

    def __init__(
        self,
        real_session,
        *,
        order_id: int,
        publisher_id: int,
        booster_id: int,
    ) -> None:
        self._real = real_session
        self._order_id = order_id
        self._publisher_id = publisher_id
        self._booster_id = booster_id
        self.injected = False
        self.conflict_seen = False
        self.competitor_id: int | None = None

    def __getattr__(self, name):
        return getattr(self._real, name)

    def begin_nested(self):
        return self._real.begin_nested()

    async def _inject_competitor(self) -> None:
        async with _session_factory() as other:
            conversation = Conversation(
                type=ConversationType.ORDER,
                order_id=self._order_id,
            )
            other.add(conversation)
            await other.flush()
            other.add_all(
                [
                    ConversationParticipant(
                        conversation_id=conversation.id,
                        user_id=self._publisher_id,
                        role_snapshot=UserRole.USER.value,
                    ),
                    ConversationParticipant(
                        conversation_id=conversation.id,
                        user_id=self._booster_id,
                        role_snapshot=UserRole.BOOSTER.value,
                    ),
                ]
            )
            await other.commit()
            self.competitor_id = conversation.id
        self.injected = True

    async def execute(self, statement, *args, **kwargs):
        # 用户行 FOR UPDATE 是建联流程里最后一次加锁；在它执行前让并发
        # 赢家先提交，确保本事务的旧快照看不到这条新会话。
        if (
            not self.injected
            and "FOR UPDATE" in str(statement)
            and "users.id" in str(statement)
        ):
            await self._inject_competitor()
        return await self._real.execute(statement, *args, **kwargs)

    async def flush(self, *args, **kwargs):
        try:
            return await self._real.flush(*args, **kwargs)
        except IntegrityError:
            self.conflict_seen = True
            raise


async def test_unique_conflict_recovery_after_rollback_returns_winner(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
) -> None:
    """唯一键冲突恢复不能因 ORM 过期对象抛 MissingGreenlet，须返回赢家会话。"""
    publisher = registered_user
    booster = booster_user
    await _top_up(client, admin_user, publisher)
    order = await _create_order(client, publisher)
    order_id = order["id"]

    publisher_obj = (
        await db_session.execute(
            select(User).where(User.id == publisher["user"]["id"])
        )
    ).scalar_one()

    session = _ConflictInjectingSession(
        db_session,
        order_id=order_id,
        publisher_id=publisher["user"]["id"],
        booster_id=booster["user"]["id"],
    )
    service = ChatService(session)

    conversation = await service.create_or_get_conversation(
        current_user=publisher_obj,
        target_user_id=booster["user"]["id"],
        order_id=order_id,
    )

    assert session.injected is True
    assert session.conflict_seen is True
    assert session.competitor_id is not None
    assert conversation.id == session.competitor_id

    # 恢复后会话仍然只有一条，参与者没有被重复插入。
    conversation_count = (
        await db_session.execute(
            select(func.count(Conversation.id)).where(Conversation.order_id == order_id)
        )
    ).scalar()
    assert conversation_count == 1
    participant_count = (
        await db_session.execute(
            select(func.count(ConversationParticipant.id)).where(
                ConversationParticipant.conversation_id == conversation.id
            )
        )
    ).scalar()
    assert participant_count == 2


async def test_order_conversation_with_other_participants_is_not_reused(
    client: AsyncClient,
    admin_user: dict,
    registered_user: dict,
    booster_user: dict,
    db_session,
    make_captcha,
) -> None:
    """订单已有别人与老板的会话时，后来者不能复用、加入或读取它。"""
    publisher = registered_user
    first_booster = booster_user
    await _top_up(client, admin_user, publisher)
    order = await _create_order(client, publisher)
    order_id = order["id"]

    first = await _open_conversation(
        client,
        publisher,
        target_user_id=first_booster["user"]["id"],
        order_id=order_id,
    )
    assert first.status_code == 200, first.text
    conversation_id = first.json()["id"]

    stranger = await _register_booster(
        client,
        db_session,
        make_captcha,
        email="other_chat_booster@example.com",
        username="OtherChatBooster",
    )

    # PENDING 订单允许任意活跃打手咨询，因此该请求能走到插入并撞唯一键；
    # 但它不是既有会话的参与者，必须得到 409 而不是复用别人的会话。
    conflict = await _open_conversation(
        client,
        stranger,
        target_user_id=publisher["user"]["id"],
        order_id=order_id,
    )
    assert conflict.status_code == 409, conflict.text
    assert "已有其他会话" in conflict.json()["detail"]

    detail = await client.get(
        f"/chat/conversations/{conversation_id}",
        headers=auth_header(stranger),
    )
    assert detail.status_code == 403, detail.text

    conversation_count = (
        await db_session.execute(
            select(func.count(Conversation.id)).where(Conversation.order_id == order_id)
        )
    ).scalar()
    assert conversation_count == 1

    participant_ids = set(
        (
            await db_session.execute(
                select(ConversationParticipant.user_id).where(
                    ConversationParticipant.conversation_id == conversation_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert participant_ids == {
        publisher["user"]["id"],
        first_booster["user"]["id"],
    }
