"""提现机会刷新规则（单行，id=1）。

管理员在后台自己设置提现机会多久恢复一次：

- 模式 A ``INTERVAL``：按**每个用户自己**滚动计时——每次提现成功后过
  N 小时才恢复 1 次机会；从没提过过的用户随时可提。
- 模式 B ``DAILY_NOON``：全站统一墙钟时间——窗口是
  ``[今天 12:00, 明天 12:00)``（产品时区 Asia/Shanghai，固定 +08:00），
  窗口内提过就没机会，到下一个 12:00 全站恢复。

无论哪种模式，每个刷新周期内每个用户只有 1 次提现机会。

被驳回（REJECTED）的提现**不占**机会（PENDING/APPROVED/PAID 都占）。

规则修改即时生效、不烙盘：每次请求现读规则现算，正在等待中的用户
按新规则重新计算。
"""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base


class WithdrawalRefreshMode(str, PyEnum):
    """提现机会刷新模式。"""

    INTERVAL = "INTERVAL"        # 每隔 N 小时（按用户上次提现起算）
    DAILY_NOON = "DAILY_NOON"    # 自然日 12:00 刷新


class WithdrawalRuleSetting(Base):
    """提现机会刷新规则（单行，id=1）。"""

    __tablename__ = "withdrawal_rule_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False, default=1)

    # 刷新模式：INTERVAL = 每隔 interval_hours 小时；DAILY_NOON = 自然日 12:00。
    mode: Mapped[WithdrawalRefreshMode] = mapped_column(
        Enum(
            WithdrawalRefreshMode,
            name="withdrawal_refresh_mode_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=WithdrawalRefreshMode.INTERVAL,
        server_default="INTERVAL",
        nullable=False,
    )

    # 模式 A 的刷新间隔（小时）：提现成功后过这么久恢复 1 次机会。
    interval_hours: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=24,
        server_default="24",
    )

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

    def __repr__(self) -> str:
        return (
            f"<WithdrawalRuleSetting mode={self.mode.value} "
            f"interval_hours={self.interval_hours}>"
        )
