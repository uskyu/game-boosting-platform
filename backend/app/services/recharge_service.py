"""充值（易支付）业务逻辑。

职责：
- 读写易支付网关配置（单行表 ``payment_settings``）；
- 为登录用户（含管理员，便于测试）创建充值订单并生成支付表单参数；
- 为回调方提供幂等入账入口（真正的余额变动仍在 ``WalletService``）。

回调地址不再由管理员配置：直接按本次充值请求的来源推导
（浏览器 ``Origin`` → 反向代理 ``X-Forwarded-*`` → ``Referer`` → ``Host``），
「用户在哪个地址上点的充值，回调就回到哪个地址」。

商户密钥只在此模块与管理员配置接口之间流转，绝不下发到用户侧接口。
"""

import json
import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.models.notification import NotificationType
from app.models.payment_setting import PAY_METHOD_NAMES, PaymentSetting
from app.models.recharge import RechargeOrder, RechargeStatus
from app.models.user import User
from app.models.wallet import WalletTransactionType
from app.services import epay_service
from app.services.notification_service import get_notification_service
from app.services.wallet_service import get_wallet_service

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")


def _pages(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size if total > 0 else 0


async def get_or_create_payment_setting(db: AsyncSession) -> PaymentSetting:
    """读取易支付配置，缺失时创建默认行（未配置凭据 → 充值不可用）。"""
    result = await db.execute(select(PaymentSetting).where(PaymentSetting.id == 1))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = PaymentSetting(
            id=1,
            min_amount=Decimal("0.01"),
            alipay_enabled=True,
            wxpay_enabled=True,
        )
        db.add(setting)
        await db.flush()
        await db.refresh(setting)
    return setting


def enabled_pay_methods(setting: PaymentSetting) -> list[dict[str, str]]:
    """已勾选的支付方式，供前端展示。"""
    return [
        {"name": PAY_METHOD_NAMES[method_type], "type": method_type}
        for method_type in setting.enabled_method_types
    ]


def is_recharge_enabled(setting: PaymentSetting) -> bool:
    """凭据齐备、总开关打开、且至少勾选一种支付方式时，用户侧才开放充值。"""
    return setting.is_configured


def _origin_from_request(request) -> str:
    """推导本站对外地址（scheme://host），用于拼接回调与跳回地址。

    优先用浏览器自带的 ``Origin``（充值 POST 一定带），它天然就是用户实际
    访问的站点地址；其次用反向代理透传头，最后才退回请求自身的 host。
    """
    origin = (request.headers.get("origin") or "").strip()
    if origin and origin != "null":
        parsed = urlparse(origin)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    if host:
        return f"{proto or request.url.scheme}://{host}"

    referer = (request.headers.get("referer") or "").strip()
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    return str(request.base_url).rstrip("/")


def build_notify_urls(request) -> tuple[str, str]:
    """返回 (notify_url, return_url)，均基于本次请求推导出的站点地址。"""
    origin = _origin_from_request(request)
    prefix = app_settings.API_V1_PREFIX.rstrip("/")
    return f"{origin}{prefix}/wallet/recharge/notify", f"{origin}/wallet"


async def create_recharge_order(
    db: AsyncSession,
    *,
    user: User,
    amount: Decimal,
    payment_method: str,
    request,
) -> tuple[RechargeOrder, str, dict[str, str]]:
    """创建充值订单并返回 (订单, 支付地址, 支付表单参数)。"""
    setting = await get_or_create_payment_setting(db)
    if not is_recharge_enabled(setting):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="管理员暂未开启充值功能",
        )

    if payment_method not in setting.enabled_method_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="支付方式不存在",
        )

    amount = Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
    min_amount = Decimal(str(setting.min_amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
    if amount < min_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"充值金额不能低于 {min_amount:.2f} 元",
        )
    if amount <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="充值金额必须大于 0",
        )

    notify_url, return_url = build_notify_urls(request)
    trade_no = epay_service.generate_trade_no(user.id)

    # 先构造支付参数：配置有问题时立刻失败，不留下无法支付的僵尸订单
    pay_url, params = epay_service.build_purchase(
        base_url=setting.pay_address,
        partner_id=setting.epay_id,
        key=setting.epay_key,
        pay_type=payment_method,
        out_trade_no=trade_no,
        name=f"账户充值 {epay_service.format_money(amount)}元",
        money=amount,
        notify_url=notify_url,
        return_url=return_url,
    )

    order = RechargeOrder(
        trade_no=trade_no,
        user_id=user.id,
        amount=amount,
        payment_method=payment_method,
        status=RechargeStatus.PENDING,
    )
    db.add(order)
    await db.flush()
    await db.refresh(order)

    logger.info(
        "Recharge order %s created: user=%s amount=%s method=%s notify=%s",
        trade_no,
        user.id,
        amount,
        payment_method,
        notify_url,
    )
    return order, pay_url, params


async def handle_notify(
    db: AsyncSession,
    *,
    params: dict[str, str],
) -> bool:
    """处理易支付异步/同步回调。

    Returns:
        True 表示已确认（可回 ``success``），False 表示应回 ``fail``。
    """
    setting = await get_or_create_payment_setting(db)
    if not setting.epay_key:
        logger.warning("Recharge notify received but epay_key is not configured")
        return False

    verified = epay_service.verify_params(params, setting.epay_key)
    if not verified["verify_status"]:
        logger.warning("Recharge notify signature mismatch: %s", verified["out_trade_no"])
        return False

    if verified["trade_status"] != epay_service.TRADE_SUCCESS:
        # 非成功状态（如等待付款）不算失败，但也不入账
        logger.info(
            "Recharge notify non-success status: %s (%s)",
            verified["trade_status"],
            verified["out_trade_no"],
        )
        return False

    trade_no = verified["out_trade_no"]
    if not trade_no:
        return False

    # 金额校验：上游回传金额必须与订单金额一致，防止金额被篡改
    notified_money = epay_service.parse_money(verified["money"])
    if notified_money is None:
        logger.warning("Recharge notify has invalid money: %s", verified["money"])
        return False

    order_result = await db.execute(
        select(RechargeOrder).where(RechargeOrder.trade_no == trade_no)
    )
    existing = order_result.scalar_one_or_none()
    if existing is None:
        logger.warning("Recharge notify for unknown order: %s", trade_no)
        return False
    if notified_money != Decimal(str(existing.amount)).quantize(
        _CENT, rounding=ROUND_HALF_UP
    ):
        logger.error(
            "Recharge notify amount mismatch for %s: notified=%s expected=%s",
            trade_no,
            notified_money,
            existing.amount,
        )
        return False

    wallet_service = get_wallet_service(db)
    completed = await wallet_service.complete_recharge(
        trade_no,
        epay_trade_no=verified["trade_no"] or None,
        notify_payload=json.dumps(params, ensure_ascii=False),
    )
    return completed is not None


async def list_user_recharges(
    db: AsyncSession,
    *,
    user_id: int,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[RechargeOrder], int]:
    wallet_service = get_wallet_service(db)
    return await wallet_service.list_recharges(
        user_id=user_id, page=page, page_size=page_size
    )


# ---------------------------------------------------------------------------
# 后台「支付记录」：全量查询 + 人工改判成功/失败
# ---------------------------------------------------------------------------

# 目标状态（管理员只能在这两个之间改判；CLOSED 是用户侧终态，后台不再切回去）
ADMIN_TARGET_SUCCESS = "SUCCESS"
ADMIN_TARGET_FAILED = "FAILED"


async def list_all_recharges(
    db: AsyncSession,
    *,
    status_filter: str | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[RechargeOrder], int, dict]:
    """后台支付记录分页查询。

    返回 (记录, 总数, 统计)。统计永远是**全量口径**（不吃筛选条件），
    与派单管理顶部汇总同思路：数字用来判断「平台整体收支」，切筛选不该让它跳。

    ``query`` 同时匹配商户单号 / 易支付上游单号 / 用户名 / 邮箱：
    老板最常问的是「他那笔到账了没」，按人等和按单号等都要能查。
    """
    conditions = []

    if status_filter:
        normalized = status_filter.strip().upper()
        try:
            conditions.append(RechargeOrder.status == RechargeStatus(normalized))
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"未知的支付状态：{status_filter}",
            ) from err

    if query and query.strip():
        term = f"%{query.strip()}%"
        # 用户名/邮箱在 users 表，走子查询避免 join 干扰分页计数
        user_ids = select(User.id).where(
            or_(User.username.ilike(term), User.email.ilike(term))
        )
        conditions.append(
            or_(
                RechargeOrder.trade_no.ilike(term),
                RechargeOrder.epay_trade_no.ilike(term),
                RechargeOrder.user_id.in_(user_ids),
            )
        )

    list_query = select(RechargeOrder)
    count_query = select(func.count(RechargeOrder.id))
    if conditions:
        list_query = list_query.where(*conditions)
        count_query = count_query.where(*conditions)

    # 统计条：不受筛选影响（全量）
    stats = await _recharge_stats(db)

    total = int((await db.execute(count_query)).scalar() or 0)
    result = await db.execute(
        list_query.order_by(RechargeOrder.created_at.desc(), RechargeOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total, stats


async def _recharge_stats(db: AsyncSession) -> dict:
    """顶部统计：总笔数 / 已到账 / 待回调 / 失败 / 已到账金额合计。"""
    rows = await db.execute(
        select(
            RechargeOrder.status,
            func.count(RechargeOrder.id),
            func.coalesce(func.sum(RechargeOrder.amount), 0),
        ).group_by(RechargeOrder.status)
    )
    counts = {"PENDING": 0, "SUCCESS": 0, "CLOSED": 0, "FAILED": 0}
    success_amount = Decimal("0.00")
    for status_value, count, amount_sum in rows.all():
        key = status_value.value if hasattr(status_value, "value") else str(status_value)
        if key in counts:
            counts[key] = int(count or 0)
        if key == RechargeStatus.SUCCESS.value:
            success_amount = Decimal(str(amount_sum or 0)).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
    return {
        "total": sum(counts.values()),
        "success": counts["SUCCESS"],
        "pending": counts["PENDING"],
        # 失败统计把「用户放弃/超时关闭」也算进去：两者都表示这笔钱没到账，
        # 老板看这一栏是为了知道「有多少笔白发了没收到钱」。
        "failed": counts["FAILED"] + counts["CLOSED"],
        "success_amount": success_amount,
    }


async def admin_update_status(
    db: AsyncSession,
    *,
    recharge_id: int,
    target_status: str,
    operator: User,
    reason: str | None = None,
) -> tuple[RechargeOrder, Decimal]:
    """管理员把一条支付记录改判为成功/失败，并按需增减余额。

    **金额只由「状态是否跨越 SUCCESS」决定，不由按钮决定**：

    ==========  ==================  ==========================
    当前状态     改为 SUCCESS         改为 FAILED
    ==========  ==================  ==========================
    PENDING     +金额（入账）          不动钱（本来没入账）
    SUCCESS     拒绝（已入账，防重复）   −金额（冲正扣回）
    FAILED      +金额（纠错补回）       拒绝（已是失败）
    CLOSED      拒绝（用户侧终态）       拒绝（已关闭且未入账）
    ==========  ==================  ==========================

    这样 PENDING 点失败绝不会凭空扣用户钱，SUCCESS 点成功也不会重复加钱。

    Returns:
        (更新后的充值单, 本次金额变动)；变动为 0 时返回 Decimal("0.00")。

    Raises:
        HTTPException: 404 单不存在；400 状态不允许/余额不足。
    """
    target = (target_status or "").strip().upper()
    if target not in (ADMIN_TARGET_SUCCESS, ADMIN_TARGET_FAILED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="只能改为成功或失败",
        )

    # 行锁：避免两个管理员同时点、或与易支付回调并发，导致重复入账/重复冲正
    result = await db.execute(
        select(RechargeOrder)
        .where(RechargeOrder.id == recharge_id)
        .with_for_update()
    )
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="支付记录不存在"
        )

    current = order.status
    amount = Decimal(str(order.amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
    wallet_service = get_wallet_service(db)
    delta = Decimal("0.00")
    note = (reason or "").strip() or None

    if target == ADMIN_TARGET_SUCCESS:
        if current == RechargeStatus.SUCCESS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该记录已是成功状态，无需重复入账",
            )
        if current == RechargeStatus.CLOSED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该记录已被用户侧关闭，不能改为成功；请让用户重新发起充值",
            )

        # PENDING / FAILED → SUCCESS：入账
        wallet = await wallet_service.get_or_create_wallet(order.user_id)
        await wallet_service.credit(
            wallet,
            amount=amount,
            tx_type=WalletTransactionType.RECHARGE,
            # 不带 recharge_order_id：该列唯一键专供易支付回调路径做幂等
            # （见 058 迁移说明）。人工入账靠 marked_at + 流水备注区分，
            # 这样「置失败 → 再置成功」可以重复执行而不会被唯一键挡住。
            operator_id=operator.id,
            remark=f"人工置成功 {order.trade_no}",
        )
        delta = amount
        order.status = RechargeStatus.SUCCESS
        if order.paid_at is None:
            order.paid_at = datetime.now(timezone.utc)

    else:  # target == FAILED
        if current == RechargeStatus.FAILED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该记录已是失败状态",
            )
        if current == RechargeStatus.CLOSED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该记录已被用户侧关闭，无需再改为失败",
            )

        if current == RechargeStatus.SUCCESS:
            # 已入账 → 必须冲正扣回（余额不足会 400，不做负数/截断）
            await wallet_service.reverse_recharge(
                order,
                operator_id=operator.id,
                remark=f"人工置失败冲正 {order.trade_no}：{note}",
            )
            delta = -amount
        # PENDING → FAILED：本来没入账，只改状态，不动钱
        order.status = RechargeStatus.FAILED

    order.marked_by = operator.id
    order.marked_at = datetime.now(timezone.utc)
    order.admin_note = note

    await db.flush()
    await db.refresh(order)

    logger.info(
        "Admin recharge decision: id=%s trade_no=%s %s -> %s delta=%s operator=%s reason=%s",
        order.id,
        order.trade_no,
        current.value,
        order.status.value,
        delta,
        operator.id,
        note,
    )
    return order, delta


async def notify_recharge_updated(
    db: AsyncSession,
    *,
    order: RechargeOrder,
    delta: Decimal,
    reason: str | None,
) -> None:
    """把人工改判结果推给用户。

    人工动了余额就必须让用户端看得到：静默改钱最后都会变成客服工单。
    通知发送失败不能影响改判本身（余额已经动了），因此这里吞掉异常只记日志。
    """
    if delta == Decimal("0.00"):
        return

    try:
        if delta > 0:
            title = "充值已到账"
            content = (
                f"充值订单 {order.trade_no} 的 ¥{delta:.2f} 已到账，"
                "已计入可用余额。"
            )
        else:
            title = "充值已撤销"
            content = (
                f"充值订单 {order.trade_no} 的 ¥{abs(delta):.2f} 已被撤销，"
                "金额已从可用余额扣回。"
            )
        if reason:
            content = f"{content}原因：{reason}"

        notification_service = get_notification_service(db)
        await notification_service.create(
            user_id=order.user_id,
            type=NotificationType.RECHARGE_UPDATED,
            title=title,
            content=content,
            link="/wallet",
            ref_id=order.id,
        )
    except Exception:  # noqa: BLE001 - 通知失败不得回滚已经发生的资金变动
        logger.exception(
            "Failed to notify user %s about recharge %s change",
            order.user_id,
            order.trade_no,
        )


__all__ = [
    "build_notify_urls",
    "admin_update_status",
    "create_recharge_order",
    "enabled_pay_methods",
    "get_or_create_payment_setting",
    "handle_notify",
    "is_recharge_enabled",
    "list_all_recharges",
    "list_user_recharges",
    "notify_recharge_updated",
    "_pages",
]
