"""
Order schemas module.
Pydantic models for order-related API request/response validation.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.order import PaymentStatus

# =============================================================================
# INPUT SCHEMAS (Request Bodies)
# =============================================================================

class OrderAnalyzeRequest(BaseModel):
    """Schema for analyzing order requirements via AI."""

    description: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="用户需求描述",
        examples=["王者荣耀，当前钻石段位，想上王者，预算500元，微信区"],
    )


class OrderCreate(BaseModel):
    """Schema for creating a new order after AI analysis."""

    game_id: int | None = Field(
        default=None,
        ge=1,
        description="游戏ID",
        examples=[1],
    )

    game_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="游戏名称",
        examples=["王者荣耀", "英雄联盟", "和平精英"],
    )

    current_rank: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="当前段位",
        examples=["钻石", "黄金", "铂金"],
    )

    target_rank: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="目标段位",
        examples=["王者", "星耀", "大师"],
    )

    price: Decimal = Field(
        ...,
        gt=0,
        le=100000,
        description="订单价格",
        examples=[500.00, 1000.00],
    )

    description_raw: str | None = Field(
        default=None,
        max_length=2000,
        description="原始需求描述",
    )

    description_ai: str | None = Field(
        default=None,
        max_length=2000,
        description="AI处理后的描述",
    )

    ai_tags: dict[str, Any] | None = Field(
        default=None,
        description="AI 提取的结构化标签",
    )

    game_account: str | None = Field(
        default=None,
        max_length=255,
        description="游戏账号",
    )

    game_password: str | None = Field(
        default=None,
        max_length=100,
        description="游戏密码",
    )

    server: str | None = Field(
        default=None,
        max_length=50,
        description="游戏区服",
        examples=["微信区", "QQ区", "艾欧尼亚"],
    )

    service_type: str | None = Field(
        default=None,
        max_length=100,
        description="服务类型",
        examples=["代练上分", "陪玩", "教学"],
    )

    role: str | None = Field(
        default=None,
        max_length=50,
        description="游戏位置/角色",
        examples=["中单", "打野", "射手"],
    )

    priority: int = Field(
        default=0,
        ge=0,
        le=10,
        description="优先级",
    )

    notes: str | None = Field(
        default=None,
        max_length=1000,
        description="备注信息",
    )

    @field_validator("price", mode="before")
    @classmethod
    def parse_price(cls, v):
        """Convert string price to Decimal."""
        if isinstance(v, str):
            # Remove currency symbols
            v = v.replace("¥", "").replace("元", "").replace(",", "").strip()
        return Decimal(str(v))

    @model_validator(mode="after")
    def validate_game_reference(self) -> "OrderCreate":
        if self.game_id is None and not self.game_name:
            raise ValueError("game_id 和 game_name 至少需要提供一个")
        return self


class OrderUpdate(BaseModel):
    """Schema for updating an existing order."""

    game_name: str | None = Field(default=None, max_length=100)
    game_id: int | None = Field(default=None, ge=1)
    current_rank: str | None = Field(default=None, max_length=50)
    target_rank: str | None = Field(default=None, max_length=50)
    price: Decimal | None = Field(default=None, gt=0, le=100000)
    description_raw: str | None = Field(default=None, max_length=2000)
    description_ai: str | None = Field(default=None, max_length=2000)
    ai_tags: dict[str, Any] | None = None
    game_account: str | None = Field(default=None, max_length=255)
    game_password: str | None = Field(default=None, max_length=100)
    service_type: str | None = Field(default=None, max_length=100)
    server: str | None = Field(default=None, max_length=100)
    priority: int | None = Field(default=None, ge=0, le=10)
    notes: str | None = Field(default=None, max_length=1000)


# =============================================================================
# OUTPUT SCHEMAS (Response Bodies)
# =============================================================================

class AIAnalysisResponse(BaseModel):
    """Schema for AI analysis result."""

    game_id: int | None = Field(default=None, description="游戏ID")
    game_name: str | None = Field(default=None, description="游戏名称")
    current_rank: str | None = Field(default=None, description="当前段位")
    target_rank: str | None = Field(default=None, description="目标段位")
    price: float | None = Field(default=None, description="预算金额")
    role: str | None = Field(default=None, description="游戏位置")
    server: str | None = Field(default=None, description="游戏区服")
    service_type: str | None = Field(default=None, description="服务类型")
    ai_tags: dict[str, Any] | None = Field(default=None, description="结构化标签")
    is_risky: bool = Field(default=False, description="是否包含违规内容")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "game_id": 1,
                "game_name": "王者荣耀",
                "current_rank": "钻石",
                "target_rank": "王者",
                "price": 500.0,
                "role": "中单",
                "server": "微信区",
                "service_type": "代练上分",
                "ai_tags": {
                    "game_id": 1,
                    "server": "微信区",
                    "service_type": "代练上分",
                    "detail": {
                        "current_rank": "钻石",
                        "target_rank": "王者",
                        "role": "中单",
                        "requirements": [],
                    },
                },
                "is_risky": False,
            }
        }
    )


class UserBrief(BaseModel):
    """Brief user information for embedding in responses."""

    id: int
    username: str
    email: str

    model_config = ConfigDict(from_attributes=True)


class OrderResponse(BaseModel):
    """Complete order response schema."""

    id: int = Field(description="订单ID")
    user_id: int = Field(description="用户ID")
    booster_id: int | None = Field(default=None, description="代练ID")
    game_id: int | None = Field(default=None, description="游戏ID")
    service_id: int | None = Field(default=None, description="服务ID")
    game_name: str = Field(description="游戏名称")
    current_rank: str = Field(description="当前段位")
    target_rank: str = Field(description="目标段位")
    price: Decimal = Field(description="订单价格")
    status: str = Field(description="订单状态")
    description_raw: str | None = Field(default=None, description="原始描述")
    description_ai: str | None = Field(default=None, description="AI描述")
    ai_tags: dict[str, Any] | None = Field(default=None, description="AI标签")
    game_account: str | None = Field(default=None, description="游戏账号")
    service_type: str | None = Field(default=None, description="服务类型")
    server: str | None = Field(default=None, description="游戏区服")
    priority: int = Field(description="优先级")
    notes: str | None = Field(default=None, description="备注")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")
    locked_at: datetime | None = Field(default=None, description="锁定时间")
    delivered_at: datetime | None = Field(default=None, description="交付时间")
    completed_at: datetime | None = Field(default=None, description="完成时间")

    payment_status: str = Field(default=PaymentStatus.UNPAID.value, description="支付状态")
    paid_at: datetime | None = Field(default=None, description="支付时间")

    # Nested user information
    user: UserBrief | None = Field(default=None, description="下单用户")
    booster: UserBrief | None = Field(default=None, description="接单代练")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "user_id": 1,
                "booster_id": None,
                "game_id": 1,
                "service_id": None,
                "game_name": "王者荣耀",
                "current_rank": "钻石",
                "target_rank": "王者",
                "price": "500.00",
                "status": "PENDING",
                "description_raw": "钻石上王者，预算500",
                "description_ai": None,
                "ai_tags": None,
                "game_account": None,
                "service_type": "代练上分",
                "server": "微信区",
                "priority": 0,
                "notes": None,
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
                "locked_at": None,
                "completed_at": None,
                "user": {"id": 1, "username": "player1", "email": "player@example.com"},
                "booster": None,
            }
        }
    )


class OrderListResponse(BaseModel):
    """Paginated list of orders response."""

    items: list[OrderResponse] = Field(description="订单列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [],
                "total": 0,
                "page": 1,
                "page_size": 20,
                "pages": 0,
            }
        }
    )
