"""
Wallet and withdrawal schemas module.
Pydantic models for wallet / withdrawal API request and response validation.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.wallet import WalletTransactionType
from app.models.withdrawal import WithdrawalChannel, WithdrawalStatus

# =============================================================================
# WALLET SCHEMAS
# =============================================================================


class WalletResponse(BaseModel):
    """Summary of the current user's wallet balances."""

    available_balance: Decimal = Field(description="可用余额")
    frozen_balance: Decimal = Field(description="冻结余额")
    total_income: Decimal = Field(description="累计收入")
    total_withdrawn: Decimal = Field(description="累计已提现")

    model_config = ConfigDict(from_attributes=True)


class WalletTransactionResponse(BaseModel):
    """Single wallet ledger entry."""

    id: int = Field(description="流水ID")
    type: WalletTransactionType = Field(description="流水类型")
    amount: Decimal = Field(description="变动金额（入账为正、扣减为负）")
    balance_before: Decimal = Field(description="变动前可用余额")
    balance_after: Decimal = Field(description="变动后可用余额")
    order_id: int | None = Field(default=None, description="关联订单ID")
    withdrawal_id: int | None = Field(default=None, description="关联提现ID")
    remark: str | None = Field(default=None, description="备注")
    created_at: datetime = Field(description="创建时间")

    model_config = ConfigDict(from_attributes=True)


class WalletTransactionListResponse(BaseModel):
    """Paginated wallet ledger entries."""

    items: list[WalletTransactionResponse] = Field(description="流水列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")


# =============================================================================
# WITHDRAWAL SCHEMAS
# =============================================================================


class WithdrawalCreateRequest(BaseModel):
    """Request body for creating a withdrawal."""

    amount: Decimal = Field(
        ...,
        ge=1,
        description="提现金额（元），最低1元",
        examples=[100.00],
    )

    channel: WithdrawalChannel = Field(
        ...,
        description="收款渠道: ALIPAY / WECHAT / BANK",
    )

    account_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="收款人姓名",
    )

    account_no: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="收款账号（支付宝/微信/银行卡号）",
    )


class WithdrawalResponse(BaseModel):
    """Single withdrawal request record."""

    id: int = Field(description="提现ID")
    user_id: int = Field(description="申请用户ID")
    amount: Decimal = Field(description="提现金额")
    channel: WithdrawalChannel = Field(description="收款渠道")
    account_name: str = Field(description="收款人姓名")
    account_no: str = Field(description="收款账号")
    status: WithdrawalStatus = Field(description="状态")
    reject_reason: str | None = Field(default=None, description="驳回原因")
    payment_reference: str | None = Field(default=None, description="打款凭证号")
    reviewed_by: int | None = Field(default=None, description="审核管理员ID")
    reviewed_at: datetime | None = Field(default=None, description="审核时间")
    paid_by: int | None = Field(default=None, description="打款管理员ID")
    paid_at: datetime | None = Field(default=None, description="打款时间")
    created_at: datetime = Field(description="申请时间")
    updated_at: datetime = Field(description="更新时间")

    model_config = ConfigDict(from_attributes=True)


class WithdrawalListResponse(BaseModel):
    """Paginated withdrawal requests."""

    items: list[WithdrawalResponse] = Field(description="提现列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")


class AdminWithdrawalResponse(WithdrawalResponse):
    """Withdrawal record enriched with applicant info for admin views."""

    username: str | None = Field(default=None, description="申请人用户名")
    user_email: str | None = Field(default=None, description="申请人邮箱")


class AdminWithdrawalListResponse(BaseModel):
    """Paginated withdrawal requests for admin views."""

    items: list[AdminWithdrawalResponse] = Field(description="提现列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")
