"""保证金模型：总开关配置（单行）与阶梯规则。

保证金玩法（阶梯权益、接单等待减免、免炸单赔付金、7 天转回冷却）只有在后台
「保证金管理」里打开总开关后才生效。总开关默认关闭，因此上线本身不改变
任何现有行为。
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class DepositSetting(Base):
    """保证金玩法总开关（单行，id=1）。"""

    __tablename__ = "deposit_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)

    # 保证金模式总开关。默认关闭：老板在后台开启后才启用全部保证金玩法。
    enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default="0"
    )

    # 保证金转回余额的冷却天数（默认 7 天，可由后台调整）
    return_cooldown_days: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=7, server_default="7"
    )

    # 订单默认炸单赔付金（元）。保证金模式开启后，发单未指定时按此值兜底；
    # 有保证金且档位免除赔付金的打手不冻结，真炸单时从保证金中扣除。
    default_compensation: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
        default=Decimal("20.00"),
        server_default="20.00",
    )

    # 结账时效计时起点（后台可选）：
    # - AFTER_DELIVERY：打手交付后开始计时，到期系统自动审核通过并结算
    #   （老板仍可提前手动审核，立即打款）
    # - AFTER_APPROVAL：老板审核通过后才开始计时，通过后再压档位对应时长结算
    settlement_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="AFTER_DELIVERY",
        server_default="AFTER_DELIVERY",
    )

    alias: Mapped[str | None] = mapped_column(String(50), nullable=True)

    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    updater: Mapped["User | None"] = relationship(
        "User", foreign_keys=[updated_by], lazy="noload"
    )


class DepositTier(Base):
    """保证金的单条阶梯规则。"""

    __tablename__ = "deposit_tiers"
    __table_args__ = (UniqueConstraint("threshold", name="uq_deposit_tiers_threshold"),)

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    # 保证金门槛（元）：余额达到该值即命中此档
    threshold: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
    )

    # 接单等待秒数：订单发布后需等待多少秒才能接单（0 = 立即可接）
    wait_seconds: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=0,
        server_default="0",
    )

    # 是否免除炸单赔付金冻结（真炸单时从保证金中扣除）
    exempt_compensation: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default="0",
    )

    # 结账时效（小时）：打手交付后经过该时长自动审核通过并结算
    settle_hours: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=72,
        server_default="72",
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default="1",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(),
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<DepositTier(id={self.id}, threshold={self.threshold}, "
            f"wait={self.wait_seconds}s, exempt={self.exempt_compensation}, "
            f"settle={self.settle_hours}h)>"
        )


# 结账时效计时起点
SETTLEMENT_MODE_AFTER_DELIVERY = "AFTER_DELIVERY"   # 交付后计时，到期自动通过
SETTLEMENT_MODE_AFTER_APPROVAL = "AFTER_APPROVAL"   # 老板通过后计时，再压一段时间
# Post-033 claims use this explicit marker when deposit rules cannot provide a
# tier snapshot. NULL remains reserved for pre-033 legacy claims.
SETTLEMENT_MODE_ORDER_DELAY = "ORDER_DELAY"
SETTLEMENT_MODES = (SETTLEMENT_MODE_AFTER_DELIVERY, SETTLEMENT_MODE_AFTER_APPROVAL)

# 默认阶梯（与老板给定的权益表一致）
# 保证金 / 接单等待秒数 / 免炸单赔付金 / 结账时效
DEFAULT_DEPOSIT_TIERS: list[dict] = [
    {"threshold": Decimal("0.00"), "wait_seconds": 30, "exempt_compensation": False, "settle_hours": 72},
    {"threshold": Decimal("100.00"), "wait_seconds": 30, "exempt_compensation": True, "settle_hours": 72},
    {"threshold": Decimal("300.00"), "wait_seconds": 20, "exempt_compensation": True, "settle_hours": 72},
    {"threshold": Decimal("500.00"), "wait_seconds": 10, "exempt_compensation": True, "settle_hours": 48},
    {"threshold": Decimal("1000.00"), "wait_seconds": 0, "exempt_compensation": True, "settle_hours": 1},
]

# 保证金转回余额的默认冷却期（天）
DEFAULT_DEPOSIT_RETURN_COOLDOWN_DAYS = 7
