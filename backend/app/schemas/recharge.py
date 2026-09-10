"""充值（易支付）与支付配置的请求/响应模型。"""

import json
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

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
# 管理员侧：支付配置
# =============================================================================


class PaymentSettingUpdate(BaseModel):
    """管理员保存易支付配置。

    ``epay_key`` 传空（None 或空串）表示保留现有密钥不变。
    """

    enabled: bool = Field(default=True, description="是否开启充值")
    pay_address: str | None = Field(default=None, max_length=500, description="支付接口地址")
    epay_id: str | None = Field(default=None, max_length=64, description="易支付商户ID")
    epay_key: str | None = Field(default=None, max_length=255, description="易支付商户密钥，留空表示不修改")
    notify_base_url: str | None = Field(default=None, max_length=500, description="回调基础地址")
    pay_methods: str | None = Field(default=None, description="支付方式 JSON 数组文本")
    min_amount: Decimal = Field(default=Decimal("1.00"), gt=0, description="单笔最低充值金额（元）")

    model_config = ConfigDict(extra="forbid")

    @field_validator("pay_address", "epay_id", "epay_key", "notify_base_url", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("pay_methods")
    @classmethod
    def normalize_methods(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        import json

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as err:
            raise ValueError("支付方式必须是合法的 JSON 数组") from err
        if not isinstance(parsed, list):
            raise ValueError("支付方式必须是 JSON 数组")
        for item in parsed:
            if not isinstance(item, dict) or not str(item.get("type") or "").strip():
                raise ValueError('支付方式每一项都必须包含 "type" 字段')
        return json.dumps(parsed, ensure_ascii=False)


class PaymentSettingResponse(BaseModel):
    """管理员读取的易支付配置。密钥只以 ``has_key`` 表示是否存在。"""

    enabled: bool
    pay_address: str | None
    epay_id: str | None
    has_key: bool = Field(description="是否已配置商户密钥")
    notify_base_url: str | None
    pay_methods: str | None
    min_amount: Decimal
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)
