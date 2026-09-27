"""Money helpers shared by services and schemas.

Pure functions only (no DB/session) so both the wallet service and the
pydantic schemas can resolve the effective service-fee rate without
import cycles.
"""

from decimal import Decimal, InvalidOperation

from app.core.config import settings

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")


def resolve_service_fee_rate(order_rate: Decimal | int | str | None) -> Decimal:
    """Resolve the effective service-fee rate for an order as a fraction.

    Per-order rate wins when set; None falls back to the platform default
    (settings.COMMISSION_RATE), preserving pre-038 orders' behaviour.

    Units: the stored per-order rate is a percentage (8 = 8%) while
    settings.COMMISSION_RATE is already a fraction (0.08). This helper always
    returns a fraction so callers keep a single unit. The result is clamped
    to [0, 1] so a bad value can never invert a payout.
    """
    if order_rate is None:
        rate = Decimal(str(settings.COMMISSION_RATE))
    else:
        rate = Decimal(str(order_rate)) / _HUNDRED
    if rate < _ZERO:
        return _ZERO
    if rate > _ONE:
        return _ONE
    return rate


def is_whole_yuan(value: Decimal | int | str | None) -> bool:
    """金额是否为整数元：100、100.00 都算整数；50.5 不算。

    归一成 Decimal 后与 to_integral_value() 比较，因此 100、'100.00'、
    Decimal('1E+4') 都算整数。None 与任何无法解析的值（含 NaN/Infinity）
    返回 False 而不抛异常。
    """
    if value is None:
        return False
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return False
    if not normalized.is_finite():
        return False
    return normalized == normalized.to_integral_value()
