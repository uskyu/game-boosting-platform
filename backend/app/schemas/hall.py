"""
大厅（Hall）相关接口的请求/响应模型。

当前承载「今日已接单」区块：列出今天已被抢的订单，每行带订单摘要、
接单打手信息与该打手的保证金余额，供大厅底部区块直接渲染。
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.schemas.serializers import serialize_datetime_utc


class RecentClaimOrder(BaseModel):
    """接单记录对应的订单摘要（大厅区块卡片用的最小字段集）。"""

    id: int = Field(description="订单ID")
    title: str | None = Field(default=None, description="订单标题")
    intro: str | None = Field(default=None, description="订单简介/需求摘要")
    price: Decimal = Field(description="订单金额")


class RecentClaimBooster(BaseModel):
    """接单打手摘要。"""

    id: int = Field(description="打手用户ID")
    username: str = Field(description="打手用户名")
    total_completed: int = Field(default=0, description="已完成（打款）单数")


class RecentClaimItem(BaseModel):
    """一条「今日已接单」记录：今天的某个 OrderClaim。"""

    id: int = Field(description="报名记录ID")
    created_at: datetime = Field(description="接单时间")
    order: RecentClaimOrder = Field(description="订单摘要")
    booster: RecentClaimBooster = Field(description="接单打手摘要")
    deposit_balance: Decimal = Field(
        default=Decimal("0.00"), description="该打手的保证金余额（无钱包记录时为 0）"
    )

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)

    model_config = ConfigDict(from_attributes=True)


class RecentClaimsResponse(BaseModel):
    """「今日已接单」区块响应：total 为时间范围内的总记录数（可能大于 items）。"""

    total: int = Field(description="总记录数")
    items: list[RecentClaimItem] = Field(description="接单记录列表（按接单时间倒序）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total": 2,
                "items": [
                    {
                        "id": 12,
                        "created_at": "2026-09-30T08:30:00Z",
                        "order": {
                            "id": 7,
                            "title": "王者荣耀 钻石上王者",
                            "intro": "微信区，三天内完成",
                            "price": "200.00",
                        },
                        "booster": {"id": 3, "username": "打手小李", "total_completed": 42},
                        "deposit_balance": "500.00",
                    }
                ],
            }
        }
    )
