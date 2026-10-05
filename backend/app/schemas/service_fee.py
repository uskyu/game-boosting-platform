"""后台「服务费设置」：全局费率与逐单服务费设置开关。"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.schemas.serializers import serialize_datetime_utc


class ServiceFeeSettingsResponse(BaseModel):
    """后台服务费设置：全局费率与是否允许逐单自定义。"""

    service_fee_rate: Decimal = Field(
        description="全局服务费费率（百分数，如 8.00 表示 8%）；0 = 不收取"
    )
    individual_service_fee_enabled: bool = Field(
        default=False,
        description="是否允许新订单逐单开关或自定义费率；关闭时新订单统一按全局费率收取",
    )
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


class ServiceFeeSettingsUpdate(BaseModel):
    """保存全局服务费设置；未提供的字段保持原值。"""

    service_fee_rate: Decimal | None = Field(
        default=None,
        ge=0,
        le=100,
        description="全局服务费费率（百分数，如 8 表示 8%）；0 = 不收取",
    )
    individual_service_fee_enabled: bool | None = Field(
        default=None,
        description="是否允许新订单逐单设置服务费；关闭时新订单统一按全局费率收取，未提供时保持原值",
    )

    model_config = ConfigDict(extra="forbid")
