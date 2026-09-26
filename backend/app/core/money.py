"""Money helpers shared by services and schemas.

Pure functions only (no DB/session) so both the wallet service and the
pydantic schemas can resolve the effective service-fee rate without
import cycles.
"""

from decimal import Decimal

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
