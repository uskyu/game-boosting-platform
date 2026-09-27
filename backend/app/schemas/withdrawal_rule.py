"""后台「提现机会刷新规则」设置。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models.withdrawal_rule import WithdrawalRefreshMode
from app.schemas.serializers import serialize_datetime_utc


class WithdrawalRuleSettingsResponse(BaseModel):
    """后台提现机会刷新规则。"""

    mode: WithdrawalRefreshMode = Field(
        description="刷新模式：INTERVAL=每隔 N 小时（按用户上次提现起算）；DAILY_NOON=自然日 12:00 刷新",
    )
    interval_hours: int = Field(description="每隔多少小时刷新一次（模式 A 生效）")
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


class WithdrawalRuleSettingsUpdate(BaseModel):
    """保存提现机会刷新规则（修改后立即生效，不烙盘）。"""

    mode: WithdrawalRefreshMode = Field(
        default=WithdrawalRefreshMode.INTERVAL,
        description="刷新模式：INTERVAL / DAILY_NOON",
    )
    interval_hours: int = Field(
        default=24, ge=1, le=168, description="每隔多少小时刷新一次（模式 A 生效）"
    )

    model_config = ConfigDict(extra="forbid")
