"""平台收入服务：订单服务费的入账与统计（仅后台可见）。

发单时发布人按毛额 ``price × max_claims`` 冻结托管，结单时打手只拿净额
``price × (1 - 费率)``；差额（服务费）通过 ``SERVICE_FEE_SETTLE`` 从发布
人冻结余额结转，并在这里留下一条平台收入流水。它不是任何用户的钱包余额，
因此单独建账、后台可查，前台接口不暴露。
"""

import logging
from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.product_time import PRODUCT_TIMEZONE, product_local_date
from app.models.order import Order
from app.models.platform_income import PlatformIncome
from app.models.user import User

logger = logging.getLogger(__name__)

_ZERO = Decimal("0.00")
_CENT = Decimal("0.01")

# 收入来源：目前只有订单服务费；预留枚举位给后续收入类型。
SOURCE_SERVICE_FEE = "SERVICE_FEE"


def month_bounds(month: str | None = None) -> tuple[datetime, datetime]:
    """返回北京自然月的 [start, end)，naive UTC（与库里的 DATETIME 比较）。

    ``month`` 为 ``YYYY-MM``；缺省取当前北京自然月。与看板其他统计口径
    一致：先按产品时区（Asia/Shanghai，固定 +08:00）划分自然月，再换算
    成 naive UTC 过滤 ``created_at``。

    Raises:
        ValueError: month 不是合法的 ``YYYY-MM``。
    """
    now = datetime.now(timezone.utc)
    if month:
        try:
            year_str, month_str = month.strip().split("-", 1)
            first = date(int(year_str), int(month_str), 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("月份格式应为 YYYY-MM") from exc
    else:
        local_today = product_local_date(now)
        first = date(local_today.year, local_today.month, 1)

    if first.month == 12:
        next_first = date(first.year + 1, 1, 1)
    else:
        next_first = date(first.year, first.month + 1, 1)

    start_local = datetime.combine(first, time.min, tzinfo=PRODUCT_TIMEZONE)
    end_local = datetime.combine(next_first, time.min, tzinfo=PRODUCT_TIMEZONE)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _to_decimal(value: Decimal | int | str | None) -> Decimal:
    """Coerce incoming amounts to Decimal (never float); None -> 0."""
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def record_order_service_fee(
    db: AsyncSession,
    order_id: int,
    booster_id: int | None,
    amount: Decimal,
    rate: Decimal | None,
) -> PlatformIncome | None:
    """记录一笔订单服务费收入（幂等）。

    唯一键 ``(order_id, booster_id, source)`` 保证同一订单的同一打手名额
    只入账一次：先查后写，并发下再捕获唯一键冲突安全跳过。金额 <= 0 或
    重复记录时返回 None。

    Args:
        amount: 服务费金额（元）。四舍五入到分。
        rate: 服务费费率（百分数，8.00 表示 8%），仅用于审计留痕。
    """
    amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
    if amount <= _ZERO:
        return None

    existing = await db.execute(
        select(PlatformIncome.id).where(
            PlatformIncome.order_id == order_id,
            PlatformIncome.booster_id == booster_id,
            PlatformIncome.source == SOURCE_SERVICE_FEE,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    normalized_rate = (
        _to_decimal(rate).quantize(_CENT, rounding=ROUND_HALF_UP)
        if rate is not None
        else None
    )
    try:
        async with db.begin_nested():
            record = PlatformIncome(
                order_id=order_id,
                booster_id=booster_id,
                amount=amount,
                service_fee_rate=normalized_rate,
                source=SOURCE_SERVICE_FEE,
            )
            db.add(record)
            await db.flush()
        return record
    except IntegrityError:
        # 并发结算先写成功：保持幂等，不重复入账。
        logger.info(
            "Order %s service fee already recorded for booster %s",
            order_id,
            booster_id,
        )
        return None


async def totals(db: AsyncSession) -> tuple[Decimal, Decimal]:
    """返回 (累计服务费, 本月服务费)，金额四舍五入到分。"""
    total_result = await db.execute(
        select(func.coalesce(func.sum(PlatformIncome.amount), 0)).where(
            PlatformIncome.source == SOURCE_SERVICE_FEE
        )
    )
    month_start, month_end = month_bounds()
    month_result = await db.execute(
        select(func.coalesce(func.sum(PlatformIncome.amount), 0)).where(
            PlatformIncome.source == SOURCE_SERVICE_FEE,
            PlatformIncome.created_at >= month_start,
            PlatformIncome.created_at < month_end,
        )
    )
    total = _to_decimal(total_result.scalar_one()).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    month_total = _to_decimal(month_result.scalar_one()).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    return total, month_total


async def list_income(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    month: str | None = None,
) -> tuple[list[tuple[PlatformIncome, str | None, str | None, str | None]], int]:
    """后台分页查询服务费流水（创建时间倒序）。

    Returns:
        (记录行列表, 总数)。每行为
        ``(PlatformIncome, order_title, game_name, booster_name)``。
    """
    conditions: list[Any] = [PlatformIncome.source == SOURCE_SERVICE_FEE]
    if month:
        month_start, month_end = month_bounds(month)
        conditions.append(PlatformIncome.created_at >= month_start)
        conditions.append(PlatformIncome.created_at < month_end)

    total_result = await db.execute(
        select(func.count(PlatformIncome.id)).where(*conditions)
    )
    total = int(total_result.scalar() or 0)

    result = await db.execute(
        select(
            PlatformIncome,
            Order.title,
            Order.game_name,
            User.username,
        )
        .join(Order, Order.id == PlatformIncome.order_id)
        .outerjoin(User, User.id == PlatformIncome.booster_id)
        .where(*conditions)
        .order_by(PlatformIncome.created_at.desc(), PlatformIncome.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.all()), total
