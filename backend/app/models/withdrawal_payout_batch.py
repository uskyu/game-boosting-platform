"""
Withdrawal payout batch model module.
Defines batch records for admin-imported payout receipts (Alipay batch
transfer), with a receipt snapshot of success/fail counts and amounts.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum

from sqlalchemy import Enum, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class WithdrawalPayoutCategory(str, PyEnum):
    """提现打款批次对应的渠道（与提现申请渠道一致，仅支持支付宝/微信）。"""

    ALIPAY = "ALIPAY"
    WECHAT = "WECHAT"


class WithdrawalPayoutBatchStatus(str, PyEnum):
    """
    打款批次生命周期：

    OPEN    -- 刚创建，等待导入打款回单
    CLOSED  -- 已导入回单（回单快照见 success_*/fail_* 字段与 reference_no）
    """

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class WithdrawalPayoutBatch(Base):
    """
    提现打款批次。

    管理员选一批审核通过的提现申请、生成批量转账文件上传给渠道
    （支付宝/微信）后，把渠道返回的批量转账结果文件导入回来，
    回单的汇总信息（笔数、金额、成功/失败拆分、批次号）快照到本表，
    并据此把对应的提现申请标记为已打款。

    ``reference_no`` 为回单上的渠道批次号（如支付宝的批次订单号）；
    ``success_*``/``fail_*`` 为导入回单时的快照，之后不会随明细变动。
    """

    __tablename__ = "withdrawal_payout_batches"

    # Primary key
    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    # 打款渠道：ALIPAY / WECHAT（与提现申请渠道一致）。
    category: Mapped[WithdrawalPayoutCategory] = mapped_column(
        Enum(
            WithdrawalPayoutCategory,
            name="withdrawal_payout_category_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    # 批次备注：管理员自制备注，一般为上传的回单文件名。
    remark: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    # 批次里的提现申请笔数 / 总金额（元），建批次时写入。
    item_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
        default=0,
        server_default="0",
    )

    # OPEN：等待导入回单；CLOSED：已导入回单并落库快照。
    status: Mapped[WithdrawalPayoutBatchStatus] = mapped_column(
        Enum(
            WithdrawalPayoutBatchStatus,
            name="withdrawal_payout_batch_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=WithdrawalPayoutBatchStatus.OPEN,
        server_default="OPEN",
        nullable=False,
        index=True,
    )

    # 渠道回单上的批次号（如支付宝批量转账的批次订单号）。
    reference_no: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # ---- 回单快照：导入成功回单时记录，之后不随明细变动 ----

    success_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    success_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=True,
    )

    fail_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    fail_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=True,
    )

    # 导入回单的管理员 user id（无外键，与 paid_by 的口径一致）。
    imported_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # 导入回单的时间。
    imported_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )

    # 创建批次的管理员 user id（无外键，同 paid_by 口径）。
    created_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<WithdrawalPayoutBatch(id={self.id}, "
            f"category={self.category.value}, status={self.status.value}, "
            f"total_amount={self.total_amount})>"
        )
