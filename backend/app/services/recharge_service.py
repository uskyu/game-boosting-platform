"""充值（易支付）业务逻辑。

职责：
- 读写易支付网关配置（单行表 ``payment_settings``）；
- 为登录用户创建充值订单并生成支付表单参数；
- 为回调方提供幂等入账入口（真正的余额变动仍在 ``WalletService``）。

商户密钥只在此模块与管理员配置接口之间流转，绝不下发到用户侧接口。
"""

import json
import logging
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.models.payment_setting import DEFAULT_PAY_METHODS_JSON, PaymentSetting
from app.models.recharge import RechargeOrder, RechargeStatus
from app.models.user import User
from app.services import epay_service
from app.services.wallet_service import get_wallet_service

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")

# 仅接受字母数字下划线的支付方式，避免把任意字符串透传给上游
_MAX_PAY_METHOD_LEN = 32


def _pages(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size if total > 0 else 0


async def get_or_create_payment_setting(db: AsyncSession) -> PaymentSetting:
    """读取易支付配置，缺失时创建默认行（未配置凭据 → 充值不可用）。"""
    result = await db.execute(
        select(PaymentSetting).where(PaymentSetting.id == 1)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = PaymentSetting(id=1, min_amount=Decimal("1.00"))
        db.add(setting)
        await db.flush()
        await db.refresh(setting)
    return setting


def parse_pay_methods(raw: str | None) -> list[dict[str, str]]:
    """解析支付方式 JSON；非法或为空时回退到默认支付方式。"""
    text = (raw or "").strip()
    if not text:
        text = DEFAULT_PAY_METHODS_JSON
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Invalid pay_methods JSON, falling back to defaults")
        parsed = json.loads(DEFAULT_PAY_METHODS_JSON)

    if not isinstance(parsed, list):
        parsed = json.loads(DEFAULT_PAY_METHODS_JSON)

    methods: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        method_type = str(item.get("type") or "").strip()
        if not method_type or len(method_type) > _MAX_PAY_METHOD_LEN:
            continue
        methods.append(
            {
                "name": str(item.get("name") or method_type).strip(),
                "type": method_type,
            }
        )
    return methods


def is_recharge_enabled(setting: PaymentSetting) -> bool:
    """三项凭据齐备且总开关打开时，用户侧才开放充值。"""
    return bool(
        setting.enabled
        and setting.pay_address
        and setting.epay_id
        and setting.epay_key
    )


def _resolve_base_url(request, setting: PaymentSetting) -> str:
    """回调基础地址：优先后台配置，未配置时回退到当前请求来源。

    生产环境务必显式配置 ``notify_base_url`` —— 反向代理下
    ``request.base_url`` 往往是内网地址，易支付服务器无法访问。
    """
    configured = (setting.notify_base_url or "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


def build_notify_urls(request, setting: PaymentSetting) -> tuple[str, str]:
    """返回 (notify_url, return_url)。"""
    base = _resolve_base_url(request, setting)
    prefix = app_settings.API_V1_PREFIX.rstrip("/")
    notify_url = f"{base}{prefix}/wallet/recharge/notify"
    return_url = f"{base}/wallet"
    return notify_url, return_url


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

    methods = parse_pay_methods(setting.pay_methods)
    if payment_method not in {m["type"] for m in methods}:
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

    notify_url, return_url = build_notify_urls(request, setting)
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
        "Recharge order %s created: user=%s amount=%s method=%s",
        trade_no,
        user.id,
        amount,
        payment_method,
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
    if notified_money != Decimal(str(existing.amount)).quantize(_CENT, rounding=ROUND_HALF_UP):
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


__all__ = [
    "build_notify_urls",
    "create_recharge_order",
    "get_or_create_payment_setting",
    "handle_notify",
    "is_recharge_enabled",
    "list_user_recharges",
    "parse_pay_methods",
    "_pages",
]
