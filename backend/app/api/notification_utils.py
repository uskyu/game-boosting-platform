"""
Utility helpers for creating and pushing notifications from API endpoints.
Keeps the endpoint code concise while ensuring every state change
triggers both a DB notification record and a real-time WebSocket push.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.post_commit import run_after_commit
from app.models.notification import Notification, NotificationType, UserPreference
from app.models.order import Order
from app.models.publisher_blacklist import PublisherBlacklist
from app.models.user import User, UserRole
from app.schemas.notification import NotificationResponse
from app.schemas.serializers import serialize_datetime_utc
from app.services import deposit_service
from app.services.connection_manager import get_connection_manager
from app.services.notification_service import get_notification_service
from app.services.web_push import push_user_notification

logger = logging.getLogger(__name__)


async def notify_user(
    db: AsyncSession,
    *,
    user_id: int,
    type: NotificationType,
    title: str,
    content: str,
    link: str | None = None,
    ref_id: int | None = None,
) -> None:
    """Create a notification in DB and push it via WebSocket if user is online."""
    svc = get_notification_service(db)

    # Check user preference
    if not await svc.should_notify(user_id, type):
        return

    notification = await svc.create(
        user_id=user_id,
        type=type,
        title=title,
        content=content,
        link=link,
        ref_id=ref_id,
    )

    # Real-time push：登记到事务提交之后执行，WebSocket 扇出不占业务事务
    cm = get_connection_manager()
    payload = NotificationResponse.model_validate(notification).model_dump(mode="json")

    async def _push_ws() -> None:
        try:
            await cm.send_notification(user_id, payload)
        except Exception:
            logger.debug("WebSocket push failed for user %s, notification saved to DB", user_id)

    await run_after_commit(_push_ws)

    should_push = (type == NotificationType.SYSTEM_ANNOUNCEMENT and "新订单" in title) or type in (NotificationType.ORDER_ACCEPTED, NotificationType.NEW_MESSAGE)
    if should_push:
        try:
            await push_user_notification(db, user_id, payload)
        except Exception:
            logger.debug("Web Push failed for user %s", user_id, exc_info=True)


@dataclass(frozen=True)
class NewOrderBroadcast:
    """发单通知后台任务所需的纯值快照。

    请求事务提交后原 ORM ``Order`` 与请求 session 都已不可用（后台任务里
    再访问会触发 MissingGreenlet），所以发单时先把字段拍平成不可变值对象。
    ``order_card`` 是大厅 slim dict，None 表示 payload 不附带卡片。
    """

    order_id: int
    publisher_id: int
    game_name: str
    designated_booster_id: int | None
    created_at: datetime
    order_card: dict | None
    exclude_user_id: int | None


async def deliver_new_order_notifications(job: NewOrderBroadcast) -> int:
    """后台任务：为新单落库通知并 WS 推送，返回实际写入条数。

    指定派单只追一个目标用户，成本极低，因此**不要求在线**：目标可能刚
    断开 WS，但通知中心仍应留下一条记录。公开单只给「当前在线且满足既有
    资格」的打手写行，离线用户不落库（大厅列表仍然能看到订单）。

    本函数由 post-commit 钩子登记为后台任务，或在无请求上下文时被
    ``run_after_commit`` 直接执行。任何失败都只记日志并返回 0，绝不抛回
    请求或让后台任务炸出未处理异常。
    """
    started = time.perf_counter()
    try:
        # 事务已提交、请求 session 已关闭：必须从工厂拿全新 session。
        # 放在函数内导入，测试可替换 app.db.session.async_session_factory。
        from app.db.session import async_session_factory

        cm = get_connection_manager()

        online_ids: set[int] = set()
        if job.designated_booster_id is None:
            online_ids = {int(uid) for uid in await cm.online_user_ids()}
            if not online_ids:
                # 没有在线打手就不必查候选、写库；大厅仍能看到新单。
                logger.info(
                    "New-order notification skipped order_id=%s "
                    "online_recipients=0 written=0 duration_ms=%.1f",
                    job.order_id,
                    (time.perf_counter() - started) * 1000,
                )
                return 0

        async with async_session_factory() as db:
            # 平台模型：管理员发单，其余活跃且允许接单的注册用户纳入通知范围。
            # 被管理员禁止接单（can_accept=False）的用户不发「新订单」通知，
            # 避免推送无法执行的抢单入口。
            # 发单人黑名单：被本单发布人拉黑的用户不发「新订单」通知（大厅仍
            # 可见），避免无效点击；用 NOT EXISTS 一次排除，不逐用户查询。
            # 公开单再叠加在线集合；指定单只按 designated_booster_id 定位，
            # 不走在线集合（离线目标也要有通知行）。
            filters = [
                User.role != UserRole.ADMIN,
                User.is_active.is_(True),
                User.can_accept.is_(True),
                ~exists(
                    select(PublisherBlacklist.id).where(
                        PublisherBlacklist.publisher_id == job.publisher_id,
                        PublisherBlacklist.blocked_user_id == User.id,
                    )
                ),
            ]
            if job.designated_booster_id is not None:
                filters.append(User.id == job.designated_booster_id)
            else:
                filters.append(User.id.in_(online_ids))

            result = await db.execute(select(User.id).where(*filters))
            recipient_ids = [int(uid) for uid in result.scalars().all()]
            if job.exclude_user_id is not None:
                recipient_ids = [
                    uid for uid in recipient_ids if uid != job.exclude_user_id
                ]

            if not recipient_ids:
                logger.info(
                    "New-order notification skipped order_id=%s "
                    "online_recipients=0 written=0 duration_ms=%.1f",
                    job.order_id,
                    (time.perf_counter() - started) * 1000,
                )
                return 0

            # 通知偏好只读校验（缺失偏好记录视为全部开启，避免逐用户懒建写库）
            pref_result = await db.execute(
                select(UserPreference).where(
                    UserPreference.user_id.in_(recipient_ids)
                )
            )
            settings = {
                pref.user_id: pref.notification_settings
                for pref in pref_result.scalars().all()
            }

            title = "新订单发布"
            if job.designated_booster_id is not None:
                content = f"有一张指定给你的新订单「{job.game_name}」，请前往接单"
            else:
                content = f"管理员发布了新订单「{job.game_name}」，请前往抢单"
            link = f"/orders/{job.order_id}"
            notification_created_at = datetime.now(timezone.utc).replace(
                microsecond=0, tzinfo=None
            )

            values = []
            for uid in recipient_ids:
                pref = settings.get(uid)
                if pref is not None and not pref.get(
                    NotificationType.SYSTEM_ANNOUNCEMENT.value, True
                ):
                    continue
                values.append({
                    "user_id": uid,
                    "type": NotificationType.SYSTEM_ANNOUNCEMENT,
                    "title": title,
                    "content": content,
                    "link": link,
                    "ref_id": job.order_id,
                    "created_at": notification_created_at,
                    "is_read": False,
                })

            if not values:
                logger.info(
                    "New-order notification skipped order_id=%s "
                    "online_recipients=%s written=0 duration_ms=%.1f",
                    job.order_id,
                    len(recipient_ids),
                    (time.perf_counter() - started) * 1000,
                )
                return 0

            async def prepare_card_variants() -> dict[int, dict] | None:
                """按保证金档位为收件人生成少量卡片变体。

                同档位用户共享同一个只读 dict 引用，不逐用户深拷贝嵌套结构。
                批量查询失败时返回空映射：通知照常发送/落库，发单不受影响。
                """
                if job.order_card is None:
                    return None
                try:
                    # 用 SAVEPOINT 包住批量读：即使查询在数据库层失败，也只
                    # 回滚这个保存点，通知仍可继续落库，不会让发单整体失败。
                    async with db.begin_nested():
                        waits = await deposit_service.get_many_tier_wait_seconds(
                            db, recipient_ids
                        )
                except Exception:
                    logger.warning(
                        "Failed to compute per-tier accept wait seconds for "
                        "new-order cards; falling back to the pending base card",
                        exc_info=True,
                    )
                    return {}
                created_at = job.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)

                cards_by_user: dict[int, dict] = {}
                cards_by_wait: dict[int, dict] = {}
                for uid in recipient_ids:
                    wait = waits.get(uid)
                    if wait is None:
                        continue
                    card = cards_by_wait.get(wait)
                    if card is None:
                        card = {
                            **job.order_card,
                            "accept_wait_seconds": wait,
                            "accept_available_at": serialize_datetime_utc(
                                created_at + timedelta(seconds=wait)
                            ),
                            "accept_window_pending": False,
                        }
                        cards_by_wait[wait] = card
                    cards_by_user[uid] = card
                return cards_by_user

            try:
                cards_by_user = await prepare_card_variants()
            except Exception:
                # 卡片变体只是体验增强：任何意外都不能让通知/发单失败
                logger.warning(
                    "Failed to prepare new-order card variants; using base card",
                    exc_info=True,
                )
                cards_by_user = None

            # 每批 500 行多值 INSERT；失败只记日志，绝不抛回请求。
            try:
                inserted_count = 0
                for offset in range(0, len(values), 500):
                    insert_result = await db.execute(
                        insert(Notification).values(values[offset:offset + 500])
                    )
                    inserted_count += insert_result.rowcount or 0
                await db.commit()
            except Exception:
                logger.warning(
                    "Failed to persist new-order notifications order_id=%s",
                    job.order_id,
                    exc_info=True,
                )
                try:
                    await db.rollback()
                except Exception:
                    logger.debug(
                        "Rollback after notification insert failure failed",
                        exc_info=True,
                    )
                return 0

            # 回读只按 ref_id + type 定位本单发出的通知（ix_notifications_ref_id），
            # 不再附带 title/created_at 等无索引条件——那会让 MySQL 退回 user_id
            # 索引扫几十万行历史通知，表越大越慢。
            result = await db.execute(
                select(
                    Notification.id,
                    Notification.user_id,
                    Notification.created_at,
                ).where(
                    Notification.ref_id == job.order_id,
                    Notification.type == NotificationType.SYSTEM_ANNOUNCEMENT,
                )
            )
            inserted = result.all()
            if len(inserted) != len(values):
                logger.warning(
                    "New-order notification row count mismatch order_id=%s "
                    "expected=%s actual=%s",
                    job.order_id,
                    len(values),
                    len(inserted),
                )

            payloads: list[tuple[int, dict]] = []
            for notification_id, user_id, row_created_at in inserted:
                payload = NotificationResponse(
                    id=notification_id,
                    user_id=user_id,
                    type=NotificationType.SYSTEM_ANNOUNCEMENT,
                    title=title,
                    content=content,
                    link=link,
                    ref_id=job.order_id,
                    is_read=False,
                    created_at=row_created_at,
                    read_at=None,
                ).model_dump(mode="json")
                card = job.order_card
                if cards_by_user is not None:
                    # 命中档位的收件人拿变体；未命中回落到基础卡
                    card = cards_by_user.get(user_id, job.order_card)
                if card is not None:
                    # 同组共享同一个只读卡片引用，避免逐条深拷贝
                    payload["order_card"] = card
                payloads.append((user_id, payload))

            # 尽力而为并发推送；单个慢/断客户端失败不影响其他收件人。
            results = await asyncio.gather(
                *(cm.send_notification(uid, payload) for uid, payload in payloads),
                return_exceptions=True,
            )
            for item in results:
                if isinstance(item, Exception):
                    logger.debug(
                        "WebSocket push failed during new-order broadcast: %r",
                        item,
                    )

            logger.info(
                "New-order notification delivered order_id=%s "
                "online_recipients=%s written=%s duration_ms=%.1f",
                job.order_id,
                len(recipient_ids),
                inserted_count,
                (time.perf_counter() - started) * 1000,
            )
            return inserted_count
    except Exception:
        logger.error(
            "New-order notification delivery failed order_id=%s duration_ms=%.1f",
            job.order_id,
            (time.perf_counter() - started) * 1000,
            exc_info=True,
        )
        return 0


async def notify_boosters_new_order(
    db: AsyncSession,
    *,
    order: Order,
    exclude_user_id: int | None = None,
    request: Request | None = None,
    order_card: dict | None = None,
) -> int:
    """
    发单通知入口：请求内只做纯值快照并登记 post-commit 后台任务。

    指定派单（``order.designated_booster_id`` 非空）只给该目标用户写通知，
    且不要求在线；公开单只给当前在线且满足既有资格的打手写行。离线用户
    不写通知行，但大厅列表仍可看到订单。

    收件人筛选、偏好读取、分批 INSERT 与 WS 推送全部移到
    ``deliver_new_order_notifications`` 的后台任务里，发单响应不再等待
    2000+ 行通知写入和全站扇出。

    Args:
        order_card: 大厅列表卡片的 slim dict，由调用方构建并复用。不为 None
            时附加到每条 WebSocket payload 的 "order_card" 键上（多份 payload
            共享同一个只读 dict 引用）；传 None 时行为与历史一致。
        request: 仅为保持原调用签名兼容；通知已移出请求事务，不再采集
            通知阶段耗时。

    Returns:
        请求上下文内返回 0（任务已登记，提交后异步执行）；没有 post-commit
        注册表时（直接调用、定时任务）立即执行并返回实际写入条数。
    """
    created_at = order.created_at
    if created_at is None:
        created_at = datetime.now(timezone.utc).replace(microsecond=0)
    job = NewOrderBroadcast(
        order_id=int(order.id),
        publisher_id=int(order.user_id),
        game_name=order.game_name,
        designated_booster_id=order.designated_booster_id,
        created_at=created_at,
        order_card=order_card,
        exclude_user_id=exclude_user_id,
    )
    # request 不再参与通知处理，保留参数只为兼容既有调用方。
    _ = request

    delivered = 0

    async def _hook() -> None:
        nonlocal delivered
        delivered = await deliver_new_order_notifications(job)

    await run_after_commit(_hook)
    return delivered
