"""后台「服务费设置」：全局服务费费率。"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.schemas.serializers import serialize_datetime_utc


class ServiceFeeSettingsResponse(BaseModel):
    """后台服务费设置：全局费率（百分数）。"""

    service_fee_rate: Decimal = Field(description="全局服务费费率（百分数，如 8.00 表示 8%）；0 = 不收取")
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


class ServiceFeeSettingsUpdate(BaseModel):
    """保存全局服务费费率（百分数）。"""

    service_fee_rate: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        le=100,
        description="全局服务费费率（百分数，如 8 表示 8%）；0 = 不收取",
    )

    model_config = ConfigDict(extra="forbid")
