"""Admin and booster-application related schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.order import OrderStatus
from app.models.user import BoosterApplicationStatus, UserRole


class BoosterApplicationResponse(BaseModel):
    user_id: int
    username: str
    email: str
    role: UserRole
    status: BoosterApplicationStatus
    game_name: str | None = None
    current_rank: str | None = None
    target_rank: str | None = None
    proof_url: str | None = None
    note: str | None = None
    booster_quota: int
    reviewed_by_admin_id: int | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None

    model_config = ConfigDict(from_attributes=True)


class BoosterApplicationReviewRequest(BaseModel):
    approve: bool = Field(description="Approve or reject this application.")
    booster_quota: int = Field(default=1, ge=0, le=50)
    review_note: str | None = Field(default=None, max_length=500)


class AdminOrderInterventionRequest(BaseModel):
    action: OrderStatus = Field(description="Target order status after intervention.")
    reason: str | None = Field(default=None, max_length=500)


class AdminOrderAssignRequest(BaseModel):
    """Request body for assigning an order to a booster."""

    booster_id: int = Field(ge=1, description="目标代练用户ID")
    reason: str | None = Field(default=None, max_length=500, description="派单备注/原因")


class AdminWithdrawalReviewRequest(BaseModel):
    """Request body for reviewing a withdrawal request."""

    action: Literal["approve", "reject"] = Field(description="审核动作")
    reason: str | None = Field(default=None, max_length=255, description="驳回原因（reject 必填）")


class AdminWithdrawalMarkPaidRequest(BaseModel):
    """Request body for marking an approved withdrawal as paid."""

    payment_reference: str = Field(
        min_length=1,
        max_length=128,
        description="打款凭证号（转账单号/流水号）",
    )


class AdminWalletAdjustRequest(BaseModel):
    """Request body for a manual wallet adjustment."""

    amount: Decimal = Field(
        description="调整金额，正数加余额、负数扣余额",
        examples=[50.00, -20.00],
    )
    reason: str = Field(min_length=1, max_length=255, description="调整原因")

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, v):
        """Convert string amounts to Decimal and reject non-finite values."""
        if isinstance(v, str):
            v = v.replace("¥", "").replace("元", "").replace(",", "").strip()
        d = Decimal(str(v))
        if not d.is_finite():
            raise ValueError("调整金额必须是有效数字")
        return d

