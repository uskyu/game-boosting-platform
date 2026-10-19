"""Recharge order model.

Records a user's self-service top-up attempt through the易支付（Epay）
gateway. 充值金额最终进入钱包 ``available_balance``（``RECHARGE`` 流水），
发单托管与提现仍照旧从余额扣减。
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class RechargeStatus(str, PyEnum):
    """
    充值订单生命周期::

        PENDING -> SUCCESS      （收到易支付成功回调，已入账）
           |  |
           |  +-> FAILED         （管理员人工置为失败，或改判失败后已冲正）
           |
           +-> CLOSED           （用户放弃 / 超时关闭，可重新发起）

    ``FAILED`` 与 ``CLOSED`` 都表示「这笔钱没到账」，但来源不同，刻意分开：
    ``CLOSED`` 是用户侧放弃/超时关闭的终态；``FAILED`` 是管理员人工裁定
    （回调失败、上游对账不符等），改判与冲正信息记在 ``marked_by`` /
    ``marked_at`` / ``admin_note`` 三列上，便于审计追溯。
    """

    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class RechargeOrder(Base):
    """A single top-up order created before redirecting to the gateway."""

    __tablename__ = "recharge_orders"
    __table_args__ = (
        Index("ix_recharge_user_created", "user_id", "created_at"),
        Index("ix_recharge_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    # 商户订单号，回调时据此定位订单；唯一键是幂等入账的最后一道防线
    trade_no: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 充值金额（元），即上游实收金额
    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
    )

    # 支付方式 type（alipay / wxpay / ...）
    payment_method: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    status: Mapped[RechargeStatus] = mapped_column(
        Enum(
            RechargeStatus,
            name="recharge_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=RechargeStatus.PENDING,
        server_default="PENDING",
        nullable=False,
        index=True,
    )

    # 上游易支付订单号（trade_no），成功回调时回填
    epay_trade_no: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # 回调原文，用于对账与排查
    notify_payload: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    # ── 管理员人工改判审计（058）──────────────────────────────────────
    # 只有后台「支付记录」人工改判成功/失败时才写入；易支付回调入账不写，
    # 因此「有 marked_at」= 这笔钱的到账状态是人定的，不是回调给的。
    marked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    marked_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    # 改判原因，最多 255 字；管理员置失败时为必填（由 schema 层强制）
    admin_note: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
        lazy="joined",
    )

    # 人工改判的管理员。与 user 同指向 users 表，必须显式指定 foreign_keys，
    # 否则 SQLAlchemy 无法在两条外键之间消歧。
    marker: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[marked_by],
        lazy="joined",
    )

    def __repr__(self) -> str:
        return (
            f"<RechargeOrder(id={self.id}, trade_no={self.trade_no}, "
            f"amount={self.amount}, status={self.status.value})>"
        )
