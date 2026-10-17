"""
After-sale schemas module.
Pydantic models for after-sale requests, images and admin review.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models.order_after_sale import AfterSaleStatus
from app.schemas.serializers import serialize_datetime_utc


class CreateAfterSaleRequest(BaseModel):
    """老板对已完成订单发起售后申请。"""

    reason: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="售后原因（5-500 字）",
    )
    amount: Decimal = Field(
        ...,
        ge=0,
        description="售后金额（元），不能超过订单派单价",
    )
    images: list[str] = Field(
        default_factory=list,
        max_length=9,
        description="凭证图片 URL（最多 9 张，须为本人上传目录下的路径）",
    )

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        """纯空白视同未填；返回去空白后的值，strip 后复检长度。"""
        reason = value.strip()
        if len(reason) < 5:
            raise ValueError("售后原因至少 5 个字")
        return reason


class AfterSaleImageOut(BaseModel):
    """Safe metadata for an after-sale proof image."""

    url: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1, le=10 * 1024 * 1024)
    content_type: str = Field(pattern=r"^image/(png|jpeg|webp)$")


class AfterSaleOrderBrief(BaseModel):
    """售后关联订单的展示摘要。"""

    id: int
    title: str | None = None
    game_name: str | None = None


class AfterSaleUserBrief(BaseModel):
    """售后相关用户（发布者/打手）的展示摘要。"""

    id: int
    username: str | None = None


class AfterSaleItem(BaseModel):
    """一条售后申请（用户视角：订单维度列表 / 我的售后）。"""

    id: int
    order_id: int
    order: AfterSaleOrderBrief | None = None
    requester_id: int
    requester: AfterSaleUserBrief | None = None
    booster_id: int
    booster: AfterSaleUserBrief | None = None
    reason: str
    images: list[str] = Field(
        default_factory=list,
        description="凭证图片 URL 列表（/uploads/aftersales/{发布者id}/ 下，最多 9 张）",
    )
    amount: Decimal
    status: AfterSaleStatus
    decision_reason: str | None = None
    reviewed_by: int | None = None
    created_at: datetime
    resolved_at: datetime | None = None

    @field_serializer("created_at", "resolved_at")
    def serialize_after_sale_datetime(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)

    model_config = ConfigDict(from_attributes=True)


class AdminAfterSaleItem(AfterSaleItem):
    """后台售后列表项：额外带发布者账号名（username），便于按账号核对。"""

    username: str | None = None


class AdminAfterSaleListResponse(BaseModel):
    """后台售后分页列表。"""

    items: list[AdminAfterSaleItem]
    total: int
    page: int
    page_size: int
    pages: int


class ReviewAfterSaleRequest(BaseModel):
    """管理员审阅售后申请。"""

    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500, description="驳回原因；reject 时必填")
