"""售后申请业务逻辑。

老板（订单发布者）可对「已完成」订单发起售后：文字原因 + 凭证图片 +
金额（≤订单派单价）。售后单 PENDING 期间该单打手禁止提现；
管理后台审阅并通过/驳回/关闭，通过时的扣款划转由 ``settle_approved_funds``
承接（打手「可用余额 → 保证金」→ 老板可用余额 + 双边流水，可按管理员裁定的
实扣额划转）；PENDING 期间老板也可主动撤回（``withdraw_after_sale``，
落 CLOSED 不动资金），该单打手也可一键认栽
（``accept_after_sale_as_booster``，按申请金额秒结划转，无需管理员审阅，
reviewed_by 留空、后台靠 decision_reason 识别）。

扣款口径（老板 2026-10-18 拍板）：先扣可用余额，不足部分从打手保证金补扣；
两处合计仍不足时**拒绝结案**并返回 400（不落状态、不动资金），让打手先充值
或缴纳保证金后重试——不再按可用余额静默截断。保证金扣减单独记
``AFTERSALE_DEPOSIT_DEDUCTION`` 流水（只动 deposit_balance），可用余额那条沿用
``AFTERSALE_DEDUCTION``，两条流水各自与 ``balance_before/after`` 对得上。

与 ``deposit_service`` 同为模块级函数风格：本模块只做规则判定与编排，
资金划转调用 ``wallet_service`` 的行锁 + 流水原语（``_lock_wallet`` /
``_apply`` / ``credit``）。
"""

import logging
from dataclasses import dataclass
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
from app.models.wallet import WalletTransaction, WalletTransactionType

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


@dataclass(frozen=True)
class AfterSaleSettlement:
    """一次售后结案的实扣拆分（金额均已量化到分）。

    约定：``from_available + from_deposit == actual == 应扣额``。
    新口径下不存在「少扣」的结案，所以没有短款字段；可扣款不足时会直接
    抛 400，售后保持 PENDING、资金零变动。
    """

    from_available: Decimal
    from_deposit: Decimal

    @property
    def actual(self) -> Decimal:
        return (self.from_available + self.from_deposit).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )


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
    accepted_by_booster: bool = False,
    settlement: "AfterSaleSettlement | None" = None,
) -> None:
    """售后结案通知：双边（老板 + 打手）都收到结论。

    approve：按视角区分文案，打手侧告知扣划结果、老板侧告知收款结果，
    金额取**实扣额**（``settlement.actual``；缺省回落到 ``settled_amount``，
    两者在新口径下恒等，仅防御旧调用方）；动用保证金抵扣时补一句分项构成，
    让打手清楚钱从可用余额扣了多少、从保证金扣了多少。
    ``accepted_by_booster`` 为真时走打手认栽秒结路径，双边改用「打手同意退款」
    口径（无管理员参与，reviewed_by 留空）。
    reject：双边同一驳回口径；close：平台未做裁定，双边告知已关闭。
    类型/link 不变。
    """
    from app.api.notification_utils import notify_user

    order_result = await db.execute(select(Order).where(Order.id == after_sale.order_id))
    order = order_result.scalar_one_or_none()
    label = _order_label(order) if order is not None else f"订单 #{after_sale.order_id}"
    link = f"/orders/{after_sale.order_id}"
    if action == "approve":
        # 实扣额优先取结算返回的分项合计：通知金额必须等于真实划转额，
        # 不能再按申请额/裁定额猜（历史 bug：短款结案时双方都被告知全额）。
        actual = (
            settlement.actual
            if settlement is not None
            else _to_decimal(settled_amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        )
        suffix = f"：{after_sale.decision_reason}" if after_sale.decision_reason else ""
        if accepted_by_booster:
            # 打手认栽口径是固定文案、不带 decision_reason，所以分项构成在这里补；
            # 只动可用余额时保留原话术（「从你的余额扣划」仍成立）。
            offset_note = ""
            if settlement is not None and settlement.from_deposit > _ZERO:
                offset_note = (
                    f"（可用余额 ¥{settlement.from_available}"
                    f" + 保证金 ¥{settlement.from_deposit}）"
                )
            booster_source = "账户" if offset_note else "余额"
            messages = {
                after_sale.booster_id: (
                    "售后申请已通过",
                    f"你已同意退款，¥{actual} 已从你的{booster_source}扣划给老板{offset_note}",
                ),
                after_sale.requester_id: (
                    "售后申请已通过",
                    f"订单「{label}」的售后申请已通过（打手同意退款），"
                    f"已收款 ¥{actual}",
                ),
            }
        else:
            # 管理员路径：suffix 已带上 decision_reason（含保证金抵扣分项），
            # 不再重复拼一遍构成，避免文案自相重复。
            messages = {
                after_sale.booster_id: (
                    "售后申请已通过",
                    f"订单「{label}」的售后申请已通过，"
                    f"已扣划 ¥{actual} 给老板{suffix}",
                ),
                after_sale.requester_id: (
                    "售后申请已通过",
                    f"订单「{label}」的售后申请已通过，已收款 ¥{actual}{suffix}",
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
    """老板对已完成/已取消订单发起售后申请。

    校验：仅发布者、订单已 COMPLETED 或 CANCELLED、金额 ≤ 订单派单价、
    凭证归属本人目录、同一订单没有 PENDING 售后（行锁 + 先查后插防并发重复发起）。
    """
    if requester.id != order.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有订单发布者可以申请售后",
        )
    if order.status not in (OrderStatus.COMPLETED, OrderStatus.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅已完成或已取消的订单可以申请售后",
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

    approve 分支先在落库前完成扣款划转（``settle_approved_funds``）：
    「可用余额 + 保证金」不足时它抛 400，整个请求事务回滚，售后保持 PENDING、
    双方余额零变动；划转成功后才落 APPROVED 并发通知，保证通知金额等于实扣额。
    动用保证金抵扣时划转会往 decision_reason 追加分项构成。
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

    # 先写 decision_reason（settle 会在其后追加保证金抵扣分项）与操作人，
    # 再扣款，最后落状态：资金划转失败（可扣款不足 400）时异常让整个请求事务
    # 回滚，售后保持 PENDING、双方余额零变动。
    after_sale.decision_reason = decision_reason
    after_sale.reviewed_by = admin.id
    settlement: AfterSaleSettlement | None = None
    if action == "approve":
        settlement = await settle_approved_funds(
            db, after_sale=after_sale, amount=settled_amount
        )
    now = datetime.now(timezone.utc)
    after_sale.status = _STATUS_BY_ACTION[action]
    after_sale.resolved_at = now
    await db.flush()
    await db.refresh(after_sale)
    logger.info(
        "After-sale %s %s by admin %s",
        after_sale.id,
        action,
        admin.id,
    )
    await _notify_after_sale_resolved(
        db,
        after_sale=after_sale,
        action=action,
        settled_amount=settled_amount,
        settlement=settlement,
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


def _append_deposit_offset_note(
    after_sale: OrderAfterSale, *, from_available: Decimal, from_deposit: Decimal
) -> None:
    """动用了保证金抵扣时，把扣款构成追加进 decision_reason（后台/售后详情可查）。

    形如「可用余额扣 ¥4.60 + 保证金抵扣 ¥15.40」；decision_reason 已有内容
    （管理员审阅意见 / 打手同意退款）时以分号拼接，截断到 500 字。
    未动用保证金时不追加，保持既有文案完全不变。
    """
    note = f"可用余额扣 ¥{from_available} + 保证金抵扣 ¥{from_deposit}"
    existing = (after_sale.decision_reason or "").strip()
    combined = f"{existing}；{note}" if existing else note
    after_sale.decision_reason = combined[:500]


async def settle_approved_funds(
    db: AsyncSession,
    *,
    after_sale: OrderAfterSale,
    amount: Decimal | None = None,
    insufficient_hint: str = "请先充值或缴纳保证金后再同意退款",
) -> AfterSaleSettlement:
    """售后通过的扣款划转：打手「可用余额 → 保证金」→ 老板可用余额 + 双边流水。

    口径（老板 2026-10-18 拍板）：
    - 应扣额 = ``amount``（未传则取售后申请金额），金额 ≤ 0 时直接返回零拆分；
    - 先扣打手可用余额，不足的部分从打手保证金补扣；
    - 两处合计仍不足 → 抛 ``HTTPException 400``：不扣任何钱、不落状态
      （调用方的状态写入随请求事务整体回滚），并在 detail 里写明缺口金额；
    - 足额时等额划进老板（requester）可用余额，平台不抽成；
    - 双边各记流水：可用余额腿 ``AFTERSALE_DEDUCTION``（``available_delta`` 为负）、
      保证金腿 ``AFTERSALE_DEPOSIT_DEDUCTION``（只动 ``deposit_balance``，
      ``balance_before == balance_after``，钱包页不会出现「金额与余额对不上」）。

    锁顺序固定「先打手后老板」（与 decide_cancel_request 的划转一致）；
    保证金与可用余额在同一行 ``wallets`` 上，加保证金腿不新增任何行锁，
    因此不会引入新的锁序依赖。校验一律在 ``_lock_wallet`` 之后进行，
    并发提现/划转排队到本事务之后，读到的余额即最终扣款依据。

    注意：保证金总开关（``DepositSetting.enabled``）关闭时**仍然照扣**——
    该余额是打手自己的钱（关闭时 transfer_out 也照样放行），不扣就形成
    「关掉开关躲售后赔付」的漏洞；开关只影响档位权益，不影响扣款本身。
    """
    # 延迟导入：wallet_service 的提现闸门反向延迟导入本模块，
    # 模块级导入会形成循环依赖（见 wallet_service.create_withdrawal）。
    from app.services.wallet_service import get_wallet_service

    required = (
        _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount is not None
        else _to_decimal(after_sale.amount).quantize(_CENT, rounding=ROUND_HALF_UP)
    )
    if required <= _ZERO:
        return AfterSaleSettlement(from_available=_ZERO, from_deposit=_ZERO)

    wallet_service = get_wallet_service(db)
    booster_wallet = await wallet_service.get_or_create_wallet(after_sale.booster_id)
    booster_wallet = await wallet_service._lock_wallet(booster_wallet.id)

    available = _to_decimal(booster_wallet.available_balance)
    deposit = _to_decimal(booster_wallet.deposit_balance)
    from_available = min(required, available).quantize(_CENT, rounding=ROUND_HALF_UP)
    remainder = required - from_available
    from_deposit = min(remainder, deposit).quantize(_CENT, rounding=ROUND_HALF_UP)
    shortfall = (required - from_available - from_deposit).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    if shortfall > _ZERO:
        # 资金与状态都不动：异常让整个请求事务回滚（app/db/session.py）。
        logger.warning(
            "After-sale %s settlement rejected: required %s, booster %s available %s, deposit %s, shortfall %s",
            after_sale.id,
            required,
            after_sale.booster_id,
            available,
            deposit,
            shortfall,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "after_sale_insufficient_funds",
                "message": (
                    f"可用余额 ¥{available} + 保证金 ¥{deposit} 不足以覆盖本次扣款 "
                    f"¥{required}，尚缺 ¥{shortfall}；{insufficient_hint}"
                ),
                "required": str(required),
                "available": str(available),
                "deposit": str(deposit),
                "shortfall": str(shortfall),
            },
        )

    # 流水级幂等兜底：状态机（售后单行锁 + 仅 PENDING + 一单一裁）已挡住绝大多数
    # 重复，这里照 deduct_compensation 的做法再查一次，把唯一键冲突变成可读的 409。
    existing = await db.execute(
        select(WalletTransaction.id).where(
            WalletTransaction.order_id == after_sale.order_id,
            WalletTransaction.booster_id == after_sale.booster_id,
            WalletTransaction.type.in_(
                (
                    WalletTransactionType.AFTERSALE_DEDUCTION,
                    WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION,
                )
            ),
        )
    )
    if existing.first() is not None:
        logger.warning(
            "After-sale %s settlement skipped: ledger already exists for order %s booster %s",
            after_sale.id,
            after_sale.order_id,
            after_sale.booster_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该售后申请已结算，请刷新后确认",
        )

    # 可用余额腿：与 consume_cancel_compensation 的可用余额路径同一原语。
    if from_available > _ZERO:
        await wallet_service._apply(
            booster_wallet,
            tx_type=WalletTransactionType.AFTERSALE_DEDUCTION,
            amount=-from_available,
            available_delta=-from_available,
            order_id=after_sale.order_id,
            booster_id=after_sale.booster_id,
            operator_id=after_sale.reviewed_by,
            remark=f"订单 #{after_sale.order_id} 售后扣划给老板",
        )
    # 保证金腿：单独一条流水只动 deposit_balance（余额快照不变），
    # 避免把两条腿挤在一条流水里导致 amount 与 balance_after 对不上。
    if from_deposit > _ZERO:
        await wallet_service._apply(
            booster_wallet,
            tx_type=WalletTransactionType.AFTERSALE_DEPOSIT_DEDUCTION,
            amount=-from_deposit,
            available_delta=_ZERO,
            deposit_delta=-from_deposit,
            order_id=after_sale.order_id,
            booster_id=after_sale.booster_id,
            operator_id=after_sale.reviewed_by,
            remark=f"订单 #{after_sale.order_id} 售后扣划（保证金抵扣）",
        )

    settlement = AfterSaleSettlement(
        from_available=from_available, from_deposit=from_deposit
    )
    actual = settlement.actual
    if from_deposit > _ZERO:
        _append_deposit_offset_note(
            after_sale, from_available=from_available, from_deposit=from_deposit
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
        "After-sale %s settled: booster %s available -%s deposit -%s, requester %s +%s (required %s)",
        after_sale.id,
        after_sale.booster_id,
        from_available,
        from_deposit,
        after_sale.requester_id,
        actual,
        required,
    )
    return settlement


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


async def accept_after_sale_as_booster(
    db: AsyncSession, *, after_sale: OrderAfterSale, booster: User
) -> OrderAfterSale:
    """打手认栽秒结：同意待处理售后，按申请金额直接划转，不等管理员。

    校验：仅该单接单打手本人、仅 PENDING；一单一裁守卫与
    ``review_after_sale`` 的 approve 分支同口径（同订单已有其他 APPROVED
    售后时 400，唯一键 uq_wallet_tx_order_booster_type 也只支持每单一条
    扣划流水）。

    落 APPROVED、decision_reason 记「打手同意退款」（已有内容拼在前面）、
    resolved_at=now；reviewed_by 保持 None——无管理员参与，后台列表靠
    decision_reason 识别打款路径。

    扣款在落库之前完成（``settle_approved_funds`` 不传 amount，按申请金额全款
    扣）：可用余额不足时自动从保证金补扣，动用保证金会把分项构成追加进
    decision_reason；「可用余额 + 保证金」仍不足时它抛 400，售后保持 PENDING、
    双方余额零变动，打手可先充值/缴纳保证金后重试。通知按**实扣额**播报
    （打手侧「你已同意退款」口径，见 ``_notify_after_sale_resolved`` 的
    ``accepted_by_booster``）。
    """
    if booster.id != after_sale.booster_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有该订单的打手可以执行此操作",
        )
    if after_sale.status != AfterSaleStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该售后申请已处理",
        )
    # 一单一裁：同管理员 approve 的权威兜底（create 侧已拦多数场景）。
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

    existing = (after_sale.decision_reason or "").strip()
    after_sale.decision_reason = (
        f"{existing}；打手同意退款" if existing else "打手同意退款"
    )[:500]
    # 先扣款、后落状态：不足额时 settle_approved_funds 抛 400 早于状态写入，
    # 异常让整个请求事务回滚 → 售后仍 PENDING、双方余额零变动、无流水。
    settlement = await settle_approved_funds(
        db,
        after_sale=after_sale,
        insufficient_hint="请先充值或缴纳保证金后再同意退款",
    )
    after_sale.status = AfterSaleStatus.APPROVED
    after_sale.resolved_at = datetime.now(timezone.utc)
    # reviewed_by 保持 None：无管理员审阅，后台靠 decision_reason 识别
    await db.flush()
    await db.refresh(after_sale)
    logger.info(
        "After-sale %s accepted by booster %s (instant settle)",
        after_sale.id,
        booster.id,
    )
    await _notify_after_sale_resolved(
        db,
        after_sale=after_sale,
        action="approve",
        settled_amount=settlement.actual,
        accepted_by_booster=True,
        settlement=settlement,
    )
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
    "accept_after_sale_as_booster",
    "admin_list_after_sales",
    "create_after_sale",
    "get_after_sale_item",
    "has_pending_after_sale_for_booster",
    "list_my_after_sales",
    "list_order_after_sales",
    "review_after_sale",
    "settle_approved_funds",
    "AfterSaleSettlement",
    "withdraw_after_sale",
]
