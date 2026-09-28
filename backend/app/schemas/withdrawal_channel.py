"""后台「提现渠道开关」设置。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.schemas.serializers import serialize_datetime_utc


class WithdrawalChannelSettingsResponse(BaseModel):
    """后台提现渠道开关。"""

    alipay_enabled: bool = Field(description="是否开放支付宝提现")
    wechat_enabled: bool = Field(description="是否开放微信提现")
    updated_by: int | None = Field(default=None, description="最后修改的管理员ID")
    updated_at: datetime | None = Field(default=None, description="最后修改时间（UTC）")

    @field_serializer("updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


class WithdrawalChannelSettingsUpdate(BaseModel):
    """保存提现渠道开关（修改后立即生效，至少修改一个开关）。"""

    alipay_enabled: bool | None = Field(default=None, description="是否开放支付宝提现")
    wechat_enabled: bool | None = Field(default=None, description="是否开放微信提现")

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _require_at_least_one(self) -> "WithdrawalChannelSettingsUpdate":
        if self.alipay_enabled is None and self.wechat_enabled is None:
            raise ValueError("请至少修改一个开关")
        return self
