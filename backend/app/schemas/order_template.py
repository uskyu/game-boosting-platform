from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OrderTemplatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    game_id: int | None = Field(default=None, ge=1)
    game_name: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=200)
    price: Decimal | None = Field(default=None, gt=0, le=100000)
    description_raw: str | None = Field(default=None, max_length=2000)
    service_type: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)
    boss_contact: str | None = Field(default=None, max_length=64)
    max_claims: int | None = Field(default=None, ge=1, le=100)
    payout_delay_days: int | None = Field(default=None, ge=0, le=30)
    payout_delay_hours: int | None = Field(default=None, ge=0, le=23)
    compensation_amount: Decimal | None = Field(default=None, gt=0, le=100000)

    @model_validator(mode="before")
    @classmethod
    def clean_empty_strings(cls, value):
        if not isinstance(value, dict):
            return value
        return {
            key: (item.strip() if isinstance(item, str) else item)
            for key, item in value.items()
            if item is not None and not (isinstance(item, str) and not item.strip())
        }
    @model_validator(mode="after")
    def require_content(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("模板至少需要填写一个字段")
        return self


class OrderTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    payload: OrderTemplatePayload


class OrderTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    payload: OrderTemplatePayload | None = None

    @model_validator(mode="after")
    def require_update(self):
        if self.name is None and self.payload is None:
            raise ValueError("模板更新不能为空")
        return self


class OrderTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime
