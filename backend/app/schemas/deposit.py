"""保证金（余额划转 / 阶梯权益）请求响应模型。"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.schemas.serializers import serialize_datetime_utc


class DepositTierResponse(BaseModel):
    """单条保证金阶梯。"""

    id: int
    threshold: Decimal = Field(description="保证金门槛（元）")
    wait_seconds: int = Field(description="接单等待秒数（0 = 立即可接）")
    exempt_compensation: bool = Field(description="是否免除炸单赔付金冻结")
    settle_hours: int = Field(description="结账时效（小时）")
    enabled: bool = Field(description="是否启用")

    model_config = ConfigDict(from_attributes=True)


class DepositTierUpsert(BaseModel):
    """后台保存单条阶梯。"""

    threshold: Decimal = Field(..., ge=0, description="保证金门槛（元）")
    wait_seconds: int = Field(..., ge=0, le=86400, description="接单等待秒数")
    exempt_compensation: bool = Field(default=False, description="是否免除炸单赔付金冻结")
    settle_hours: int = Field(..., ge=0, le=8760, description="结账时效（小时）")
    enabled: bool = Field(default=True, description="是否启用")

    model_config = ConfigDict(extra="forbid")


class DepositTierReplaceRequest(BaseModel):
    """整表替换阶梯（后台的阶梯编辑器直接提交完整列表）。"""

    tiers: list[DepositTierUpsert] = Field(..., min_length=1, max_length=50)

    model_config = ConfigDict(extra="forbid")


class DepositTierListResponse(BaseModel):
    tiers: list[DepositTierResponse]


class DepositOverviewResponse(BaseModel):
    """我的保证金概览：余额、当前档位与全部阶梯权益。"""

    enabled: bool = Field(description="保证金模式是否已开启（后台总开关）")
    deposit_balance: Decimal = Field(description="当前保证金余额（元）")
    available_balance: Decimal = Field(description="可用余额（元）")

    current_threshold: Decimal | None = Field(
        default=None, description="当前命中档位的门槛，用于前端高亮"
    )
    wait_seconds: int = Field(default=0, description="当前档位的接单等待秒数")
    exempt_compensation: bool = Field(default=False, description="当前档位是否免除炸单赔付金")
    settle_hours: int = Field(default=72, description="当前档位的结账时效（小时）")

    can_return: bool = Field(description="当前是否允许转回余额")
    return_block_reason: str | None = Field(
        default=None, description="不允许转回时的中文原因"
    )

    tiers: list[DepositTierResponse] = Field(default_factory=list, description="全部生效阶梯")


class DepositTransferRequest(BaseModel):
    """保证金划转请求（转入 / 转回）。"""

    amount: Decimal = Field(..., gt=0, description="金额（元）", examples=[100.00])

    model_config = ConfigDict(extra="forbid")


class DepositTierAdminResponse(DepositTierResponse):
    """后台视角的阶梯（含更新时间）。"""

    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


class DepositTierAdminListResponse(BaseModel):
    tiers: list[DepositTierAdminResponse]


# =============================================================================
# 后台「保证金管理」
# =============================================================================


class DepositSettingsAdminResponse(BaseModel):
    """后台保证金管理：总开关 + 冷却天数 + 完整阶梯。"""

    enabled: bool = Field(description="保证金模式总开关")
    return_cooldown_days: int = Field(description="保证金转回余额的冷却天数")
    default_compensation: Decimal = Field(description="订单默认炸单赔付金（元）")
    settlement_mode: str = Field(description="结账时效计时起点：AFTER_DELIVERY / AFTER_APPROVAL")
    updated_at: datetime

    tiers: list[DepositTierAdminResponse] = Field(default_factory=list)

    @field_serializer("updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


class DepositSettingsAdminUpdate(BaseModel):
    """保存保证金管理配置（总开关 + 阶梯整表替换）。"""

    enabled: bool = Field(default=False, description="保证金模式总开关")
    return_cooldown_days: int = Field(
        default=7, ge=0, le=365, description="转回余额冷却天数"
    )
    default_compensation: Decimal = Field(
        default=Decimal("20.00"), ge=0, le=100000, description="订单默认炸单赔付金（元）"
    )
    settlement_mode: str = Field(
        default="AFTER_DELIVERY", description="结账时效计时起点：AFTER_DELIVERY / AFTER_APPROVAL"
    )

    @field_validator("settlement_mode")
    @classmethod
    def validate_settlement_mode(cls, value: str) -> str:
        from app.models.deposit import SETTLEMENT_MODES

        if value not in SETTLEMENT_MODES:
            raise ValueError("结账时效计时起点取值不合法")
        return value
    tiers: list[DepositTierUpsert] = Field(..., min_length=1, max_length=50)

    model_config = ConfigDict(extra="forbid")
