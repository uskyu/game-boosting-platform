"""售后申请业务逻辑。

老板（订单发布者）可对「已完成」订单发起售后：文字原因 + 凭证图片 +
金额（≤订单派单价）。售后单 PENDING 期间该单打手禁止提现；
管理后台审阅并通过/驳回/关闭，通过时的扣款划转由 ``settle_approved_funds``
承接（打手可用余额 → 老板余额 + 双边流水，可按管理员裁定的实扣额划转）；
PENDING 期间老板也可主动撤回（``withdraw_after_sale``，落 CLOSED 不动资金）。

与 ``deposit_service`` 同为模块级函数风格：本模块只做规则判定与编排，
资金划转调用 ``wallet_service`` 的行锁 + 流水原语（``_lock_wallet`` /
``_apply`` / ``credit``）。
"""

import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.security import escape_like
from app.models.notification import NotificationType
from app.models.order import Order, OrderStatus
from app.models.order_after_sale import AfterSaleStatus, OrderAfterSale
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")
_MAX_IMAGES = 9

# 我的售后的视角：requester=老板（发布者）视角，booster=打手视角
_ROLE_REQUESTER = "requester"
_ROLE_BOOSTER = "booster"

# 管理员审阅三动作 → 落库状态；close 与撤回均为「平台未做裁定」的关结态
_STATUS_BY_ACTION = {
    "approve": AfterSaleStatus.APPROVED,
    "reject": AfterSaleStatus.REJECTED,
    "close": AfterSaleStatus.CLOSED,
}


def _to_decimal(value: Decimal | int | str | None) -> Decimal:
    if value is None or value == "":
        return _ZERO
    return Decimal(str(value))


def _image_url_prefix(user_id: int) -> str:
    return f"/uploads/aftersales/{user_id}/"


def _validate_image_url(url: str, *, user_id: int) -> str:
    """售后凭证必须留在申请人自己的上传目录下（仿提现二维码归属校验）。"""
    prefix = _image_url_prefix(user_id)
    if not url.startswith(prefix) or len(url) <= len(prefix):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="售后凭证图片路径无效",
        )
    filename = url[len(prefix):]
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="售后凭证图片路径无效",
        )
    return url


def _normalize_images(images: list[str] | None, *, user_id: int) -> list[str]:
    """校验归属 + 去重，保持上传顺序；超额在 Schema 层已拦，这里兜底。"""
    if not images:
        return []
    if len(images) > _MAX_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"售后凭证最多上传{_MAX_IMAGES}张图片",
        )
    normalized: list[str] = []
    for image in images:
        url = _validate_image_url(str(image), user_id=user_id)
        if url not in normalized:
            normalized.append(url)
    return normalized


def _order_label(order: Order) -> str:
    return order.title or order.game_name or f"订单 #{order.id}"


def _row_to_item(
    after_sale: OrderAfterSale,
    order_title: str | None,
    order_game_name: str | None,
    requester_username: str | None,
    booster_username: str | None,
    *,
    username: str | None = None,
) -> dict[str, Any]:
    """把 (售后单, 订单标题/游戏名, 双方用户名) 行组装成前端展示结构。"""
    item: dict[str, Any] = {
        "id": after_sale.id,
        "order_id": after_sale.order_id,
        "order": {
            "id": after_sale.order_id,
            "title": order_title,
            "game_name": order_game_name,
        },
        "requester_id": after_sale.requester_id,
        "requester": {"id": after_sale.requester_id, "username": requester_username},
        "booster_id": after_sale.booster_id,
        "booster": {"id": after_sale.booster_id, "username": booster_username},
        "reason": after_sale.reason,
        "images": list(after_sale.images or []),
        "amount": after_sale.amount,
        "status": after_sale.status,
        "decision_reason": after_sale.decision_reason,
        "reviewed_by": after_sale.reviewed_by,
        "created_at": after_sale.created_at,
        "resolved_at": after_sale.resolved_at,
    }
    if username is not None:
        item["username"] = username
    return item


def _after_sale_query():
    """售后列表统一 join：订单摘要 + 发布者/打手用户名，禁止 N+1。

    返回 (query, 发布者别名, 打手别名)：调用方需要按用户名筛选时直接用别名，
    避免二次 join users 造成歧义。
    """
    requester = aliased(User)
    booster = aliased(User)
    query = (
        select(
            OrderAfterSale,
            Order.title,
            Order.game_name,
            requester.username,
            booster.username,
        )
        .join(Order, Order.id == OrderAfterSale.order_id)
        .join(requester, requester.id == OrderAfterSale.requester_id)
        .join(booster, booster.id == OrderAfterSale.booster_id)
    )
    return query, requester, booster


async def _notify_after_sale_created(
    db: AsyncSession,
    *,
    after_sale: OrderAfterSale,
    order: Order,
    requester: User,
) -> None:
    """售后发起通知：打手 + 全体活跃管理员（管理后台路径）。"""
    # 延迟导入避免 service -> api 的循环依赖
    from app.api.notification_utils import notify_user

    label = _order_label(order)
    await notify_user(
        db,
        user_id=after_sale.booster_id,
        type=NotificationType.AFTERSALES_CREATED,
        title="售后申请待处理",
        content=f"订单「{label}」的老板发起了售后申请，售后处理期间暂不能提现",
        link=f"/orders/{order.id}",
        ref_id=after_sale.id,
    )
    admin_result = await db.execute(
        select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    )
    for (admin_id,) in admin_result.all():
        if admin_id == requester.id:
            continue
        await notify_user(
            db,
            user_id=admin_id,
            type=NotificationType.AFTERSALES_CREATED,
            title="售后申请待处理",
            content=(
                f"订单 #{order.id}「{label}」被 {requester.username} "
                f"发起售后申请（¥{after_sale.amount}）"
            ),
            link="/admin?tab=afterSales",
            ref_id=after_sale.id,
        )


async def _notify_after_sale_resolved(
    db: AsyncSession,
    *,
    after_sale: OrderAfterSale,
    action: str,
    settled_amount: Decimal | None = None,
) -> None:
    """售后结案通知：双边（老板 + 打手）都收到结论。

    approve：按视角区分文案，打手侧告知扣划结果、老板侧告知收款结果，
    金额取裁定实扣额；reject：双边同一驳回口径；close：平台未做裁定，
    双边告知已关闭。不足额说明（decision_reason 里的差额备注）双方都带上。
    类型/link 不变。
    """
    from app.api.notification_utils import notify_user

    order_result = await db.execute(select(Order).where(Order.id == after_sale.order_id))
    order = order_result.scalar_one_or_none()
    label = _order_label(order) if order is not None else f"订单 #{after_sale.order_id}"
    link = f"/orders/{after_sale.order_id}"
    if action == "approve":
        settled = _to_decimal(settled_amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        suffix = f"：{after_sale.decision_reason}" if after_sale.decision_reason else ""
        messages = {
            after_sale.booster_id: (
                "售后申请已通过",
                f"订单「{label}」的售后申请已通过，"
                f"已扣划 ¥{settled} 给老板{suffix}",
            ),
            after_sale.requester_id: (
                "售后申请已通过",
                f"订单「{label}」的售后申请已通过，已收款 ¥{settled}{suffix}",
            ),
        }
    elif action == "close":
        content = f"订单「{label}」的售后申请已关闭，平台未做裁定"
        if after_sale.decision_reason:
            content += f"：{after_sale.decision_reason}"
        messages = {
            user_id: ("售后申请已关闭", content)
            for user_id in (after_sale.requester_id, after_sale.booster_id)
        }
    else:
        content = f"订单「{label}」的售后申请被驳回"
        if after_sale.decision_reason:
            content += f"：{after_sale.decision_reason}"
        messages = {
            user_id: ("售后申请已驳回", content)
            for user_id in (after_sale.requester_id, after_sale.booster_id)
        }
    for user_id, (title, content) in messages.items():
        await notify_user(
            db,
            user_id=user_id,
            type=NotificationType.AFTERSALES_RESOLVED,
            title=title,
            content=content,
            link=link,
            ref_id=after_sale.id,
        )


async def _notify_after_sale_withdrawn(
    db: AsyncSession,
    *,
    after_sale: OrderAfterSale,
) -> None:
    """老板撤诉通知：双边确认撤回，打手侧说明提现限制已解除。

    类型沿用 AFTERSALES_RESOLVED（平台未做裁定，区别于通过/驳回）。
    """
    from app.api.notification_utils import notify_user

    order_result = await db.execute(select(Order).where(Order.id == after_sale.order_id))
    order = order_result.scalar_one_or_none()
    label = _order_label(order) if order is not None else f"订单 #{after_sale.order_id}"
    messages = {
        after_sale.booster_id: (
            "售后申请已撤回",
            f"订单「{label}」的售后申请已撤回，提现限制已解除",
        ),
        after_sale.requester_id: (
            "售后申请已撤回",
            f"订单「{label}」的售后申请已撤回，平台不再处理该售后",
        ),
    }
    for user_id, (title, content) in messages.items():
        await notify_user(
            db,
            user_id=user_id,
            type=NotificationType.AFTERSALES_RESOLVED,
            title=title,
            content=content,
            link=f"/orders/{after_sale.order_id}",
            ref_id=after_sale.id,
        )


async def create_after_sale(
    db: AsyncSession,
    *,
    order: Order,
    requester: User,
    reason: str,
    amount: Decimal,
    images: list[str] | None,
) -> OrderAfterSale:
    """老板对已完成订单发起售后申请。

    校验：仅发布者、订单已 COMPLETED、金额 ≤ 订单派单价、凭证归属本人目录、
    同一订单没有 PENDING 售后（行锁 + 先查后插防并发重复发起）。
    """
    if requester.id != order.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有订单发布者可以申请售后",
        )
    if order.status != OrderStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅已完成的订单可以申请售后",
        )
    if order.booster_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该订单没有接单打手，无法申请售后",
        )

    reason_text = (reason or "").strip()
    if len(reason_text) < 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="售后原因至少 5 个字",
        )
    amount_value = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
    if amount_value < _ZERO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="售后金额不能为负数",
        )
    if amount_value > _to_decimal(order.price):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="售后金额不能超过订单派单价",
        )
    image_urls = _normalize_images(images, user_id=requester.id)

    pending_result = await db.execute(
        select(OrderAfterSale.id)
        .where(
            OrderAfterSale.order_id == order.id,
            OrderAfterSale.status == AfterSaleStatus.PENDING,
        )
        .with_for_update()
        .limit(1)
    )
    if pending_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该订单已有待处理的售后申请",
        )
    # 一单只允许通过一笔售后：既有 APPROVED 说明该单已裁定扣划过，
    # 再发起会重复扣款（wallet_transactions 的 (order_id, booster_id, type)
    # 唯一键也只支持每单一类流水一条）。
    approved_result = await db.execute(
        select(OrderAfterSale.id)
        .where(
            OrderAfterSale.order_id == order.id,
            OrderAfterSale.status == AfterSaleStatus.APPROVED,
        )
        .limit(1)
    )
    if approved_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该订单已有已裁定通过的售后申请，不能再次发起",
        )

    after_sale = OrderAfterSale(
        order_id=order.id,
        requester_id=requester.id,
        booster_id=order.booster_id,
        reason=reason_text[:500],
        images=image_urls,
        amount=amount_value,
        status=AfterSaleStatus.PENDING,
    )
    db.add(after_sale)
    await db.flush()
    await db.refresh(after_sale)
    logger.info(
        "After-sale %s created for order %s by publisher %s, amount=%s",
        after_sale.id,
        order.id,
        requester.id,
        amount_value,
    )
    await _notify_after_sale_created(db, after_sale=after_sale, order=order, requester=requester)
    return after_sale


async def list_order_after_sales(db: AsyncSession, order_id: int) -> list[dict[str, Any]]:
    """单个订单的全部售后记录（创建时间倒序）。"""
    query, _requester, _booster = _after_sale_query()
    result = await db.execute(
        query
        .where(OrderAfterSale.order_id == order_id)
        .order_by(OrderAfterSale.created_at.desc(), OrderAfterSale.id.desc())
    )
    return [
        _row_to_item(
            after_sale, order_title, order_game_name, requester_name, booster_name
        )
        for after_sale, order_title, order_game_name, requester_name, booster_name
        in result.all()
    ]


async def get_after_sale_item(db: AsyncSession, after_sale_id: int) -> dict[str, Any] | None:
    """单条售后的展示结构（创建/审核后回显用，与列表同一套 join）。"""
    query, _requester, _booster = _after_sale_query()
    result = await db.execute(query.where(OrderAfterSale.id == after_sale_id))
    row = result.first()
    if row is None:
        return None
    return _row_to_item(*row)


async def list_my_after_sales(
    db: AsyncSession,
    user_id: int,
    role: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """我的售后：role=requester（老板视角，默认）/ booster（打手视角）。"""
    query, _requester, _booster = _after_sale_query()
    if role == _ROLE_BOOSTER:
        query = query.where(OrderAfterSale.booster_id == user_id)
    else:
        query = query.where(OrderAfterSale.requester_id == user_id)
    if status:
        try:
            status_value = AfterSaleStatus(status.strip().upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="售后状态参数无效",
            )
        query = query.where(OrderAfterSale.status == status_value)
    result = await db.execute(
        query.order_by(OrderAfterSale.created_at.desc(), OrderAfterSale.id.desc())
    )
    return [
        _row_to_item(
            after_sale, order_title, order_game_name, requester_name, booster_name
        )
        for after_sale, order_title, order_game_name, requester_name, booster_name
        in result.all()
    ]


async def admin_list_after_sales(
    db: AsyncSession,
    *,
    status: str | None = None,
    order_id: int | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """后台售后列表：状态/订单/关键词筛选 + 分页（创建时间倒序）。"""
    query, requester, booster = _after_sale_query()
    conditions = []
    if status:
        try:
            conditions.append(OrderAfterSale.status == AfterSaleStatus(status.strip().upper()))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="售后状态参数无效",
            )
    if order_id is not None:
        conditions.append(OrderAfterSale.order_id == order_id)
    if q and q.strip():
        token = q.strip()
        pattern = f"%{escape_like(token)}%"
        matchers = [
            Order.title.ilike(pattern),
            requester.username.ilike(pattern),
            booster.username.ilike(pattern),
        ]
        numeric = token.lstrip("#")
        if numeric.isdigit():
            matchers.append(Order.id == int(numeric))
        conditions.append(or_(*matchers))

    total_result = await db.execute(
        select(func.count(OrderAfterSale.id))
        .select_from(OrderAfterSale)
        .join(Order, Order.id == OrderAfterSale.order_id)
        .join(requester, requester.id == OrderAfterSale.requester_id)
        .join(booster, booster.id == OrderAfterSale.booster_id)
        .where(*conditions)
    )
    total = int(total_result.scalar() or 0)

    result = await db.execute(
        query
        .where(*conditions)
        .order_by(OrderAfterSale.created_at.desc(), OrderAfterSale.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        _row_to_item(
            after_sale,
            order_title,
            order_game_name,
            requester_name,
            booster_name,
            username=requester_name,
        )
        for after_sale, order_title, order_game_name, requester_name, booster_name
        in result.all()
    ]
    return items, total


async def review_after_sale(
    db: AsyncSession,
    *,
    after_sale: OrderAfterSale,
    admin: User,
    action: str,
    reason: str | None,
    amount: Decimal | None = None,
) -> OrderAfterSale:
    """管理员审阅售后申请：通过（可调裁定金额）/驳回/关闭 + 双边通知。

    approve：实扣额 = ``amount``（未传则取售后申请金额），必须满足
    ``0 < 实扣额 ≤ after_sale.amount``；低于申请额时把裁定说明追加进
    decision_reason，再由 ``settle_approved_funds`` 按实扣额划转。
    reject/close：必填原因、不动资金；close 落 CLOSED（平台未做裁定）。

    approve 分支在状态落库之后调用 ``settle_approved_funds`` 承接扣款划转，
    且先行于结案通知：不足额时划转会往 decision_reason 追加差额说明，
    通知里才能带上最终扣划结果。
    """
    if after_sale.status != AfterSaleStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该售后申请已处理",
        )
    if action not in _STATUS_BY_ACTION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="操作仅支持 approve、reject 或 close",
        )
    decision_reason = (reason or "").strip()[:500] or None
    if action != "approve" and decision_reason is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{'驳回' if action == 'reject' else '关闭'}售后申请必须填写原因",
        )

    settled_amount: Decimal | None = None
    if action == "approve":
        requested = _to_decimal(after_sale.amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        settled_amount = (
            _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
            if amount is not None
            else requested
        )
        if settled_amount <= _ZERO or settled_amount > requested:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="裁定扣款金额不能超过售后申请金额",
            )
        if settled_amount < requested:
            decision_reason = _append_adjustment_note(
                decision_reason, settled=settled_amount, requested=requested
            )
        # 一单一裁：同订单已有其他 APPROVED 售后时拒绝（wallet_transactions 的
        # (order_id, booster_id, type) 唯一键也只支持每单一条扣划流水）。
        # create 侧已拦多数场景，这里是并发双裁的权威兜底。
        other_approved = await db.execute(
            select(OrderAfterSale.id)
            .where(
                OrderAfterSale.order_id == after_sale.order_id,
                OrderAfterSale.status == AfterSaleStatus.APPROVED,
                OrderAfterSale.id != after_sale.id,
            )
            .limit(1)
        )
        if other_approved.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该订单已有已通过的售后申请，不能重复裁定",
            )

    now = datetime.now(timezone.utc)
    after_sale.status = _STATUS_BY_ACTION[action]
    after_sale.decision_reason = decision_reason
    after_sale.reviewed_by = admin.id
    after_sale.resolved_at = now
    await db.flush()
    await db.refresh(after_sale)
    logger.info(
        "After-sale %s %s by admin %s",
        after_sale.id,
        action,
        admin.id,
    )
    if action == "approve":
        # 先落扣款划转（不足额时会往 decision_reason 追加差额说明），
        # 再发双边通知，保证通知里能带上最终扣划结果。
        await settle_approved_funds(db, after_sale=after_sale, amount=settled_amount)
    await _notify_after_sale_resolved(
        db, after_sale=after_sale, action=action, settled_amount=settled_amount
    )
    return after_sale


def _append_adjustment_note(
    decision_reason: str | None, *, settled: Decimal, requested: Decimal
) -> str:
    """管理员裁定实扣额低于申请额时，把裁定说明追加进 decision_reason。

    形如「裁定扣款 ¥20.00（申请 ¥30.00）」；已有内容（管理员审阅意见）时
    以分号拼接，截断到 500 字。
    """
    note = f"裁定扣款 ¥{settled}（申请 ¥{requested}）"
    existing = (decision_reason or "").strip()
    combined = f"{existing}；{note}" if existing else note
    return combined[:500]


def _append_shortfall_note(
    after_sale: OrderAfterSale, *, requested: Decimal, actual: Decimal
) -> None:
    """可用余额不足时把差额追加进 decision_reason。

    形如「打手可用余额不足 ¥X.XX，按实际余额 ¥Y.YY 扣收」；decision_reason
    已有内容（管理员审阅意见）时以分号拼接，无内容时不带分号前缀。
    """
    shortfall = (requested - actual).quantize(_CENT, rounding=ROUND_HALF_UP)
    note = f"打手可用余额不足 ¥{shortfall}，按实际余额 ¥{actual} 扣收"
    existing = (after_sale.decision_reason or "").strip()
    combined = f"{existing}；{note}" if existing else note
    after_sale.decision_reason = combined[:500]


async def settle_approved_funds(
    db: AsyncSession, *, after_sale: OrderAfterSale, amount: Decimal | None = None
) -> None:
    """售后通过的扣款划转：打手可用余额 → 老板可用余额 + 双边流水。

    口径（与取消赔偿划转同一套钱包原语，全部走 wallet_service 的行锁 +
    流水原语）：
    - 实扣额 = ``amount``（未传则取售后申请金额），只扣打手可用余额：
      可用 ≥ 实扣额时扣实扣额，不足则扣至 0；
    - 实扣金额等额划进老板（requester）可用余额；
    - 双边各记一条 wallet_transactions 流水（扣款记负、收款记正），
      balance_before/after 快照双方各自的可用余额；
    - 不足额时把差额追加进 decision_reason，通知与后台留痕都能看到。

    行锁顺序固定「先打手后老板」（与 decide_cancel_request 的划转一致），
    且打手钱包行锁住之后才读可用余额算实扣，并发提现/划转排队到本事务之后。
    """
    # 延迟导入：wallet_service 的提现闸门反向延迟导入本模块，
    # 模块级导入会形成循环依赖（见 wallet_service.create_withdrawal）。
    from app.models.wallet import WalletTransactionType
    from app.services.wallet_service import get_wallet_service

    effective = (
        _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount is not None
        else _to_decimal(after_sale.amount).quantize(_CENT, rounding=ROUND_HALF_UP)
    )
    if effective <= _ZERO:
        return None

    wallet_service = get_wallet_service(db)
    booster_wallet = await wallet_service.get_or_create_wallet(after_sale.booster_id)
    booster_wallet = await wallet_service._lock_wallet(booster_wallet.id)
    actual = min(effective, _to_decimal(booster_wallet.available_balance)).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    if actual < effective:
        logger.warning(
            "After-sale %s deduction capped: requested %s but booster %s available %s",
            after_sale.id,
            effective,
            after_sale.booster_id,
            booster_wallet.available_balance,
        )
        _append_shortfall_note(after_sale, requested=effective, actual=actual)
    if actual <= _ZERO:
        await db.flush()
        return None

    # 打手扣款：与 consume_cancel_compensation 的可用余额路径同一原语
    # （行锁 + balance_before/after 快照 + 流水），只动可用余额。
    await wallet_service._apply(
        booster_wallet,
        tx_type=WalletTransactionType.AFTERSALE_DEDUCTION,
        amount=-actual,
        available_delta=-actual,
        order_id=after_sale.order_id,
        booster_id=after_sale.booster_id,
        operator_id=after_sale.reviewed_by,
        remark=f"订单 #{after_sale.order_id} 售后扣划给老板",
    )
    # 老板收款：与取消赔偿入账同一原语（credit 只对 ORDER_INCOME 累计收入，
    # 售后收款不计入累计收入）。
    requester_wallet = await wallet_service.get_or_create_wallet(after_sale.requester_id)
    await wallet_service.credit(
        requester_wallet,
        amount=actual,
        tx_type=WalletTransactionType.AFTERSALE_REFUND,
        order_id=after_sale.order_id,
        booster_id=after_sale.booster_id,
        operator_id=after_sale.reviewed_by,
        remark=f"订单 #{after_sale.order_id} 售后收款（打手 #{after_sale.booster_id}）",
    )
    await db.flush()
    logger.info(
        "After-sale %s settled: booster %s -%s, requester %s +%s (requested %s)",
        after_sale.id,
        after_sale.booster_id,
        actual,
        after_sale.requester_id,
        actual,
        effective,
    )


async def withdraw_after_sale(
    db: AsyncSession, *, after_sale: OrderAfterSale, user: User
) -> OrderAfterSale:
    """老板撤回待处理售后：仅发布者本人、仅 PENDING 可撤，不动资金。

    落 CLOSED、decision_reason 写死「发布人主动撤回」并记 resolved_at
    （无管理员审阅，reviewed_by 留空），双边通知：打手侧说明提现限制
    已解除，老板侧确认撤回。
    """
    if user.id != after_sale.requester_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有售后申请发布者可以撤回售后",
        )
    if after_sale.status != AfterSaleStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该售后申请已处理，无法撤回",
        )

    after_sale.status = AfterSaleStatus.CLOSED
    after_sale.decision_reason = "发布人主动撤回"
    after_sale.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(after_sale)
    logger.info(
        "After-sale %s withdrawn by publisher %s",
        after_sale.id,
        user.id,
    )
    await _notify_after_sale_withdrawn(db, after_sale=after_sale)
    return after_sale


async def has_pending_after_sale_for_booster(db: AsyncSession, booster_id: int) -> bool:
    """该打手是否仍有待处理售后（PENDING）：命中即禁止提现。"""
    result = await db.execute(
        select(OrderAfterSale.id)
        .where(
            OrderAfterSale.booster_id == booster_id,
            OrderAfterSale.status == AfterSaleStatus.PENDING,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


__all__ = [
    "admin_list_after_sales",
    "create_after_sale",
    "get_after_sale_item",
    "has_pending_after_sale_for_booster",
    "list_my_after_sales",
    "list_order_after_sales",
    "review_after_sale",
    "settle_approved_funds",
    "withdraw_after_sale",
]
