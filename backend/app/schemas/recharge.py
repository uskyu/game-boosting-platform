"""充值（易支付）与支付配置的请求/响应模型。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.models.recharge import RechargeStatus
from app.schemas.serializers import serialize_datetime_utc

# =============================================================================
# 用户侧：充值
# =============================================================================


class PayMethodOption(BaseModel):
    """可选的支付方式（type 对应易支付 ``type`` 参数）。"""

    name: str = Field(description="展示名称")
    type: str = Field(description="支付方式标识，如 alipay / wxpay")


class RechargeConfigResponse(BaseModel):
    """充值入口配置。绝不包含商户密钥。"""

    enabled: bool = Field(description="是否已开启充值")
    pay_methods: list[PayMethodOption] = Field(default_factory=list, description="支持的支付方式")
    min_amount: Decimal = Field(description="单笔最低充值金额（元）")


class RechargeCreateRequest(BaseModel):
    """用户发起充值的请求体。"""

    amount: Decimal = Field(..., gt=0, description="充值金额（元）", examples=[100.00])
    payment_method: str = Field(..., min_length=1, max_length=32, description="支付方式")

    model_config = ConfigDict(extra="forbid")

    @field_validator("payment_method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请选择支付方式")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        if any(ch not in allowed for ch in value):
            raise ValueError("支付方式格式不正确")
        return value


class RechargeCreateResponse(BaseModel):
    """发起充值后前端所需的支付表单信息。"""

    trade_no: str = Field(description="商户订单号")
    amount: Decimal = Field(description="充值金额（元）")
    pay_url: str = Field(description="支付网关地址，前端以 POST 表单提交")
    params: dict[str, str] = Field(description="支付表单参数（已含签名）")


class RechargeResponse(BaseModel):
    """单条充值记录。"""

    id: int = Field(description="充值订单ID")
    trade_no: str = Field(description="商户订单号")
    amount: Decimal = Field(description="充值金额（元）")
    payment_method: str = Field(description="支付方式")
    status: RechargeStatus = Field(description="状态")
    created_at: datetime = Field(description="创建时间")
    paid_at: datetime | None = Field(default=None, description="到账时间")

    @field_serializer("created_at", "paid_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)

    model_config = ConfigDict(from_attributes=True)


class RechargeListResponse(BaseModel):
    """分页充值记录。"""

    items: list[RechargeResponse] = Field(description="充值记录")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")


# =============================================================================
# 管理员侧：支付记录（充值记录全量 + 人工改判）
# =============================================================================


class AdminRechargeUser(BaseModel):
    """充值记录里的用户摘要（够管理员认人即可，不下发敏感字段）。"""

    id: int = Field(description="用户ID")
    username: str = Field(description="用户名")
    email: str = Field(description="邮箱")


class AdminRechargeResponse(BaseModel):
    """后台单条支付记录。

    除充值单本身字段外，额外给出足以判断「回调到底成没成」的信息：
    ``epay_trade_no`` 有值 = 易支付确实回调过；``marked_at`` 有值 = 这笔的
    到账状态是管理员人工改判的，不是回调给的。
    """

    id: int = Field(description="充值订单ID")
    trade_no: str = Field(description="商户订单号")
    amount: Decimal = Field(description="充值金额（元）")
    payment_method: str = Field(description="支付方式")
    status: RechargeStatus = Field(description="状态：PENDING/SUCCESS/CLOSED/FAILED")
    user: AdminRechargeUser | None = Field(default=None, description="发起充值的用户")
    created_at: datetime = Field(description="发起时间")
    paid_at: datetime | None = Field(default=None, description="到账时间（回调入账或人工置成功）")
    epay_trade_no: str | None = Field(default=None, description="易支付上游订单号，有值表示回调已收到")
    notify_payload: str | None = Field(default=None, description="回调原文，用于对账排查")
    marked_by: AdminRechargeUser | None = Field(default=None, description="人工改判的管理员")
    marked_at: datetime | None = Field(default=None, description="人工改判时间")
    admin_note: str | None = Field(default=None, description="人工改判原因")

    @field_serializer("created_at", "paid_at", "marked_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)

    model_config = ConfigDict(from_attributes=True)


class AdminRechargeStats(BaseModel):
    """顶部统计：全部 / 已到账 / 待回调 / 失败。"""

    total: int = Field(description="充值单总数")
    success: int = Field(description="已到账笔数")
    pending: int = Field(description="待支付/待回调笔数")
    failed: int = Field(description="失败笔数")
    success_amount: Decimal = Field(description="已到账金额合计（元）")


class AdminRechargeListResponse(BaseModel):
    """后台分页支付记录 + 顶部统计条。"""

    items: list[AdminRechargeResponse] = Field(description="支付记录")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    pages: int = Field(description="总页数")
    # 统计条：**不受当前筛选影响**，永远给全量口径（与派单管理顶部汇总同思路）
    stats: AdminRechargeStats = Field(description="全量统计")


class AdminRechargeStatusUpdate(BaseModel):
    """管理员把一条支付记录改判为成功或失败。

    金额变动由服务端按「状态是否跨越」判定，前端只表达目标状态；
    置失败时 ``reason`` 必填，避免出现无据可查的资金扣减。
    """

    status: Literal["SUCCESS", "FAILED"] = Field(
        description="目标状态：SUCCESS=改为成功并入账；FAILED=改为失败（已入账的冲正扣回）"
    )
    reason: str | None = Field(
        default=None,
        max_length=255,
        description="改判原因；改为失败时必填，改为成功时可选",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def require_reason_for_failure(self):
        if self.status == "FAILED" and not self.reason:
            raise ValueError("改为失败必须填写原因")
        return self


# =============================================================================
# 管理员侧：支付配置
# =============================================================================

class PaymentSettingUpdate(BaseModel):
    """管理员保存易支付配置。

    ``epay_key`` 传空（None 或空串）表示保留现有密钥不变。
    回调地址不需要配置：按充值请求来源自动推导。
    """

    enabled: bool = Field(default=True, description="是否开启充值")
    pay_address: str | None = Field(default=None, max_length=500, description="支付接口地址")
    epay_id: str | None = Field(default=None, max_length=64, description="易支付商户ID")
    epay_key: str | None = Field(default=None, max_length=255, description="易支付商户密钥，留空表示不修改")
    alipay_enabled: bool = Field(default=True, description="启用支付宝")
    wxpay_enabled: bool = Field(default=True, description="启用微信")
    min_amount: Decimal = Field(default=Decimal("0.01"), gt=0, description="单笔最低充值金额（元），默认 1 分钱")

    model_config = ConfigDict(extra="forbid")

    @field_validator("pay_address", "epay_id", "epay_key", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class PaymentSettingResponse(BaseModel):
    """管理员读取的易支付配置。密钥只以 ``has_key`` 表示是否存在。"""

    enabled: bool
    pay_address: str | None
    epay_id: str | None
    has_key: bool = Field(description="是否已配置商户密钥")
    alipay_enabled: bool = Field(description="是否启用支付宝")
    wxpay_enabled: bool = Field(description="是否启用微信")
    min_amount: Decimal
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)
