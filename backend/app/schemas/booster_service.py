"""
Booster service schemas module.
Pydantic models for marketplace service cards.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_required_text(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("字段不能为空")
    return cleaned


def _normalize_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    normalized: list[str] = []
    for tag in tags:
        cleaned = tag.strip()
        if not cleaned:
            raise ValueError("标签不能为空")
        normalized.append(cleaned)
    return normalized


class BoosterServiceCreate(BaseModel):
    """Schema for creating a booster service."""

    game_id: int = Field(..., ge=1, description="游戏ID")
    title: str = Field(..., min_length=1, max_length=150, description="服务标题")
    description: str | None = Field(default=None, max_length=2000, description="服务描述")
    service_type: str = Field(..., min_length=1, max_length=100, description="服务类型")
    price_per_hour: Decimal = Field(..., gt=0, le=100000, description="每小时价格")
    tags: list[str] | None = Field(default=None, description="自定义标签")

    model_config = ConfigDict(extra="forbid")

    @field_validator("title", "service_type")
    @classmethod
    def validate_required_text_fields(cls, value: str) -> str:
        return _normalize_required_text(value)

    @field_validator("description")
    @classmethod
    def validate_optional_description(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None) -> list[str] | None:
        return _normalize_tags(value)

    @field_validator("price_per_hour", mode="before")
    @classmethod
    def parse_price_per_hour(cls, value: Any) -> Decimal:
        if isinstance(value, str):
            value = value.replace("¥", "").replace("元", "").replace(",", "").strip()
        return Decimal(str(value))


class BoosterServiceUpdate(BaseModel):
    """Schema for updating a booster service."""

    game_id: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    service_type: str | None = Field(default=None, min_length=1, max_length=100)
    price_per_hour: Decimal | None = Field(default=None, gt=0, le=100000)
    tags: list[str] | None = None
    is_available: bool | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("title", "service_type")
    @classmethod
    def validate_optional_required_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_required_text(value)

    @field_validator("description")
    @classmethod
    def validate_optional_description(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None) -> list[str] | None:
        return _normalize_tags(value)

    @field_validator("price_per_hour", mode="before")
    @classmethod
    def parse_price_per_hour(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.replace("¥", "").replace("元", "").replace(",", "").strip()
        return Decimal(str(value))


class BoosterServiceOrderCreate(BaseModel):
    """Schema for creating an order from a service card."""

    description_raw: str | None = Field(default=None, max_length=2000, description="需求描述")
    description_ai: str | None = Field(default=None, max_length=2000, description="AI 描述")
    price: Decimal | None = Field(default=None, gt=0, le=100000, description="确认价格")
    estimated_hours: Decimal | None = Field(default=None, gt=0, le=1000, description="预估时长")
    current_rank: str | None = Field(default=None, max_length=50, description="当前段位")
    target_rank: str | None = Field(default=None, max_length=50, description="目标段位")
    game_account: str | None = Field(default=None, max_length=255, description="游戏账号")
    game_password: str | None = Field(default=None, max_length=100, description="游戏密码")
    notes: str | None = Field(default=None, max_length=1000, description="备注")
    server: str | None = Field(default=None, max_length=100, description="区服")
    ai_tags: dict[str, Any] | None = Field(default=None, description="结构化标签")

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "description_raw",
        "description_ai",
        "current_rank",
        "target_rank",
        "game_account",
        "game_password",
        "notes",
        "server",
    )
    @classmethod
    def validate_optional_text_fields(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("price", "estimated_hours", mode="before")
    @classmethod
    def parse_decimal_fields(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.replace("¥", "").replace("元", "").replace(",", "").strip()
        return Decimal(str(value))


class BoosterServiceResponse(BaseModel):
    """Booster service response schema."""

    id: int = Field(description="服务ID")
    booster_id: int = Field(description="代练ID")
    game_id: int = Field(description="游戏ID")
    title: str = Field(description="服务标题")
    description: str | None = Field(default=None, description="服务描述")
    service_type: str = Field(description="服务类型")
    price_per_hour: Decimal = Field(description="每小时价格")
    tags: list[str] | None = Field(default=None, description="服务标签")
    is_available: bool = Field(description="是否上架")
    order_count: int = Field(description="已完成订单数")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")

    model_config = ConfigDict(from_attributes=True)


class BoosterServiceListResponse(BaseModel):
    """Paginated booster service list response."""

    items: list[BoosterServiceResponse] = Field(description="服务列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")

    model_config = ConfigDict(from_attributes=True)
