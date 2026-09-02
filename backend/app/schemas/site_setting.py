"""Schemas for public and administrator site settings APIs."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SiteSettingUpdate(BaseModel):
    site_name: str = Field(..., min_length=1, max_length=200)
    site_description: str | None = Field(default=None, max_length=5000)

    model_config = ConfigDict(extra="forbid")

    @field_validator("site_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("站点名称不能为空")
        return value

    @field_validator("site_description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class SiteSettingResponse(BaseModel):
    id: int
    site_name: str
    site_description: str | None
    site_logo_url: str | None
    favicon_url: str | None
    updated_by: int | None
    updated_at: datetime
    logo_recommendation: str = "建议使用 512×512 以上的 PNG、JPEG 或 WebP 图片，文件不超过 10MB。"

    model_config = ConfigDict(from_attributes=True)
