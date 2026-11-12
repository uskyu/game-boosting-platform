"""保证金（余额划转 / 阶梯权益）请求响应模型。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models.wallet import WalletTransactionType
from app.schemas.serializers import serialize_datetime_utc


class DepositTierResponse(BaseModel):
    """单条保证金阶梯。"""

    id: int
    threshold: Decimal = Field(description="保证金门槛（元）")
    wait_seconds: int = Field(description="接单等待秒数（0 = 立即可接）")
    exempt_compensation: bool = Field(description="是否免除炸单赔付金冻结")
    settle_hours: int | None = Field(
        default=None,
        description="结账时效（小时）；null = 该档不承诺加速，按订单到账时效倒计时",
    )
    daily_accept_limit: int | None = Field(
        default=None,
        description="每日接单量上限（按北京时间每日 0:00 刷新；NULL/0 = 不限）",
    )
    enabled: bool = Field(description="是否启用")

    model_config = ConfigDict(from_attributes=True)


class DepositTierUpsert(BaseModel):
    """后台保存单条阶梯。"""

    threshold: Decimal = Field(..., ge=0, description="保证金门槛（元）")
    wait_seconds: int = Field(..., ge=0, le=86400, description="接单等待秒数")
    exempt_compensation: bool = Field(default=False, description="是否免除炸单赔付金冻结")
    # 留空（null）= 该档不承诺加速，完全按打手所接订单的到账时效倒计时。
    # 老板 2026-10-18 R3：档位只是可选加速器，订单到账时效才是基准。
    settle_hours: int | None = Field(
        default=None,
        ge=0,
        le=8760,
        description="结账时效（小时）；留空 = 按订单到账时效",
    )
    daily_accept_limit: int | None = Field(
        default=None,
        ge=0,
        le=999,
        description="每日接单量上限（0 与 None 同义 = 不限）",
    )
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
    settle_hours: int | None = Field(
        default=None,
        description="当前档位的结账时效（小时）；null = 该档不承诺加速，按订单到账时效",
    )
    daily_accept_limit: int | None = Field(
        default=None,
        description="当前档位的每日接单量上限（NULL/0 = 不限）",
    )
    daily_accepted_count: int = Field(
        default=0, description="我今日窗口（北京时间 0:00 刷新）已接且未取消的订单数"
    )
    daily_limit_reset_at: datetime | None = Field(
        default=None, description="每日接单量下次刷新时间（窗口起点 + 1 天，UTC）"
    )

    can_return: bool = Field(description="当前是否允许转回余额")
    return_block_reason: str | None = Field(
        default=None, description="不允许转回时的中文原因"
    )
    # 冷却天数由后台「保证金管理」配置（默认 7 天）。前端要把它写进说明文案，
    # 让打手在转回之前就知道要等多久，不必等点下去被拒才看到原因。
    return_cooldown_days: int = Field(
        default=7, description="保证金转回余额的冷却天数（后台可配）"
    )

    tiers: list[DepositTierResponse] = Field(default_factory=list, description="全部生效阶梯")

    @field_serializer("daily_limit_reset_at")
    def serialize_reset_at(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


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
    global_booster_quota: int = Field(description="全局接单配额：所有用户同时处理订单上限")
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
        default="AFTER_DELIVERY", description="结账时效起点：AFTER_DELIVERY / AFTER_APPROVAL"
    )
    global_booster_quota: int = Field(
        default=5, ge=1, le=1000, description="全局接单配额：所有用户同时处理订单上限"
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


# =============================================================================
# 后台「保证金流水」
# =============================================================================


class AdminDepositLedgerStats(BaseModel):
    """查询区间内保证金转入/转回汇总（不随 direction 过滤变化）。"""

    in_amount: Decimal = Field(description="转入保证金合计（元）")
    in_count: int = Field(description="转入保证金笔数")
    out_amount: Decimal = Field(description="保证金转回合计（元）")
    out_count: int = Field(description="保证金转回笔数")
    out_users: int = Field(description="发生保证金转回的用户数")
    self_out_amount: Decimal = Field(description="其中用户自助转回金额（不含管理员调整）")
    self_out_count: int = Field(description="其中用户自助转回笔数")
    self_out_users: int = Field(description="其中用户自助转回的用户数")
    net_amount: Decimal = Field(description="净变化 = 转入 - 转回（元）")


class AdminDepositLedgerItem(BaseModel):
    """单条保证金划转流水。"""

    id: int = Field(description="流水ID")
    user_id: int = Field(description="用户ID")
    username: str = Field(description="用户名")
    email: str = Field(description="用户邮箱")
    type: WalletTransactionType = Field(description="流水类型")
    direction: Literal["in", "out"] = Field(description="方向：in=转入保证金，out=转回余额")
    available_change: Decimal = Field(
        description="可用余额变动（用户自助转入为负、转回为正；管理员调保证金不动可用余额，恒为 0）"
    )
    deposit_change: Decimal = Field(description="保证金变动（转入为正、转回为负）")
    available_before: Decimal = Field(description="变动前可用余额")
    available_after: Decimal = Field(description="变动后可用余额")
    current_deposit_balance: Decimal = Field(description="该用户当前保证金余额")
    remark: str | None = Field(default=None, description="备注")
    order_id: int | None = Field(default=None, description="关联订单ID（管理员调整通常为空）")
    operator_id: int | None = Field(default=None, description="操作管理员ID；空=用户自助操作")
    operator_username: str | None = Field(default=None, description="操作管理员用户名")
    created_at: datetime = Field(description="发生时间（UTC）")

    @field_serializer("created_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)


class AdminDepositLedgerResponse(BaseModel):
    """后台保证金流水列表 + 区间汇总。"""

    items: list[AdminDepositLedgerItem] = Field(description="流水列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")
    start_date: date = Field(description="实际查询起始日期（北京时间）")
    end_date: date = Field(description="实际查询结束日期（北京时间）")
    stats: AdminDepositLedgerStats = Field(description="区间汇总")

    @field_serializer("start_date", "end_date")
    def serialize_day(self, value: date | None) -> str | None:
        return value.isoformat() if value else None
