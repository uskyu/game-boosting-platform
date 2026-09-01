"""
Game schemas module.
Pydantic models for game catalog API validation and serialization.
"""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.game import GameCategory, GamePlatform


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


def _normalize_string_list(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("列表项不能为空")
        normalized.append(cleaned)
    return normalized


class GameServiceTemplate(BaseModel):
    """Structured JSON schema for game service templates."""

    service_types: list[str] = Field(
        ...,
        min_length=1,
        description="可用服务类型列表",
    )
    has_rank_system: bool = Field(description="是否存在段位系统")
    rank_tiers: list[str] = Field(default_factory=list, description="段位列表")
    servers: list[str] = Field(default_factory=list, description="区服列表")
    roles: list[str] = Field(default_factory=list, description="可选位置/职业列表")
    custom_fields: list[str] = Field(default_factory=list, description="额外字段列表")

    @field_validator(
        "service_types",
        "rank_tiers",
        "servers",
        "roles",
        "custom_fields",
    )
    @classmethod
    def validate_string_lists(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)

    @model_validator(mode="after")
    def validate_rank_configuration(self) -> "GameServiceTemplate":
        if self.has_rank_system and not self.rank_tiers:
            raise ValueError("存在段位系统时必须提供 rank_tiers")
        return self


class GameBase(BaseModel):
    """Shared game fields for create/update/response schemas."""

    name: str = Field(..., min_length=1, max_length=100, description="游戏中文名")
    english_name: str | None = Field(default=None, max_length=150, description="游戏英文名")
    category: GameCategory = Field(description="游戏分类")
    platform: GamePlatform = Field(description="游戏平台")
    icon_url: str | None = Field(default=None, max_length=500, description="图标地址")
    cover_url: str | None = Field(default=None, max_length=500, description="封面地址")
    color_theme: str | None = Field(default=None, max_length=7, description="主题色")
    service_template: GameServiceTemplate = Field(description="服务模板")
    description: str | None = Field(default=None, max_length=100, description="一句话简介")
    is_active: bool = Field(default=True, description="是否上架")
    sort_order: int = Field(default=0, ge=0, description="排序权重")

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_required_text(value)

    @field_validator("english_name", "icon_url", "cover_url", "description")
    @classmethod
    def validate_optional_text_fields(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("color_theme")
    @classmethod
    def validate_color_theme(cls, value: str | None) -> str | None:
        cleaned = _normalize_optional_text(value)
        if cleaned is None:
            return None
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", cleaned) is None:
            raise ValueError("color_theme 必须是 #RRGGBB 格式")
        return cleaned


class GameCreate(GameBase):
    """Schema for creating a game."""


class GameUpdate(BaseModel):
    """Schema for updating a game."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    english_name: str | None = Field(default=None, max_length=150)
    category: GameCategory | None = None
    platform: GamePlatform | None = None
    icon_url: str | None = Field(default=None, max_length=500)
    cover_url: str | None = Field(default=None, max_length=500)
    color_theme: str | None = Field(default=None, max_length=7)
    service_template: GameServiceTemplate | None = None
    description: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def validate_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_required_text(value)

    @field_validator("english_name", "icon_url", "cover_url", "description")
    @classmethod
    def validate_optional_text_fields(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("color_theme")
    @classmethod
    def validate_optional_color_theme(cls, value: str | None) -> str | None:
        cleaned = _normalize_optional_text(value)
        if cleaned is None:
            return None
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", cleaned) is None:
            raise ValueError("color_theme 必须是 #RRGGBB 格式")
        return cleaned


class GameResponse(GameBase):
    """Full game response schema."""

    id: int = Field(description="游戏ID")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")

    model_config = ConfigDict(from_attributes=True)


class GameListResponse(BaseModel):
    """Game list response schema."""

    items: list[GameResponse] = Field(description="游戏列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")

    model_config = ConfigDict(from_attributes=True)
