"""Schemas for public and administrator site settings APIs."""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer
from app.core.title_rules import normalize_rules
from app.schemas.serializers import serialize_datetime_utc

# 页脚 HTML 里绝不能出现的片段：脚本、内嵌框架、伪协议。
# 这段 HTML 会被 v-html 渲染给所有访客，管理员账号一旦被盗就是全站 XSS，
# 所以在保存处做一次最小拦截（不做完整清洗，避免把正常标签也吃掉）。
_FOOTER_HTML_FORBIDDEN = ("<script", "<iframe", "<object", "<embed", "javascript:")
_FOOTER_HTML_EVENT_ATTR = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


def validate_footer_html(value: str | None) -> str | None:
    """校验页脚 HTML：去首尾空白，拒绝脚本类内容；空串表示不显示。"""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(fragment in lowered for fragment in _FOOTER_HTML_FORBIDDEN):
        raise ValueError("页脚 HTML 不能包含脚本、内嵌框架或 javascript: 链接")
    if _FOOTER_HTML_EVENT_ATTR.search(text):
        raise ValueError("页脚 HTML 不能包含 onclick 等事件属性")
    return text


class OrderTitleReplacementRule(BaseModel):
    """一条订单标题关键词替换规则。"""

    original: str = Field(min_length=1, max_length=50, description="原标题关键词")
    replacement: str = Field(
        default="", max_length=100, description="替换文本；空串表示删除该关键词"
    )

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SiteSettingUpdate(BaseModel):
    site_name: str = Field(..., min_length=1, max_length=200)
    site_description: str | None = Field(default=None, max_length=5000)
    # 大厅「今日已接单」区块全站开关：不传保持不变（管理员可显式开关）
    hall_recent_claims_enabled: bool | None = Field(
        default=None,
        description="大厅是否显示「今日已接单」区块（全站生效）",
    )
    # 安全设置：不传保持不变
    registration_captcha_enabled: bool | None = Field(
        default=None,
        description="注册时是否要求图形验证码",
    )
    accept_captcha_enabled: bool | None = Field(
        default=None,
        description="打手接单时是否要求图形验证码",
    )
    max_online_devices: int | None = Field(
        default=None,
        ge=0,
        le=20,
        description="同一账号最多同时在线设备数，0 表示不限制",
    )
    order_title_replacements: list[OrderTitleReplacementRule] | None = Field(
        default=None,
        description=(
            "订单标题关键词替换规则；不传保持不变，空数组表示关闭全部替换"
        ),
    )
    footer_html: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "页脚最底部自定义 HTML（备案号等）；不传保持不变，空串表示清空不显示"
        ),
    )
    # 新注册用户权限默认值：不传保持不变（显式 false 必须生效）
    registration_default_can_publish: bool | None = Field(
        default=None,
        description="新注册用户默认是否有发单权限（仅影响之后注册的账号）",
    )
    registration_default_can_accept: bool | None = Field(
        default=None,
        description="新注册用户默认是否有接单权限（仅影响之后注册的账号）",
    )
    publish_permission_apply_enabled: bool | None = Field(
        default=None,
        description="是否允许无发单权限的用户自助提交申请",
    )
    # 全站发单权限总开关：不传保持不变；显式 false 表示全站开放发单
    publish_permission_required: bool | None = Field(
        default=None,
        description=(
            "是否要求非管理员已开通发单权限才能发单；"
            "false=全站开放（仅封禁发单的用户仍被拦截）"
        ),
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("order_title_replacements")
    @classmethod
    def validate_replacements(
        cls, value: list[OrderTitleReplacementRule] | None
    ) -> list[OrderTitleReplacementRule] | None:
        if value is None:
            return None
        seen: set[str] = set()
        for rule in value:
            if not rule.original:
                raise ValueError("关键词不能为空")
            if rule.original in seen:
                raise ValueError(f"关键词重复：{rule.original}")
            seen.add(rule.original)
        return value

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

    @field_validator("footer_html")
    @classmethod
    def validate_footer(cls, value: str | None) -> str | None:
        return validate_footer_html(value)


class SiteSettingResponse(BaseModel):
    id: int
    site_name: str
    site_description: str | None
    site_logo_url: str | None
    favicon_url: str | None
    # 大厅「今日已接单」区块全站开关（默认开；公开可读，大厅据此渲染）
    hall_recent_claims_enabled: bool = True
    # 安全设置（公开可读：注册页/接单弹窗据此渲染）
    registration_captcha_enabled: bool = True
    accept_captcha_enabled: bool = False
    max_online_devices: int = 2
    order_title_replacements: list[OrderTitleReplacementRule] = Field(
        default_factory=list,
        description="订单标题关键词替换规则（NULL 时返回默认规则）",
    )
    footer_html: str | None = Field(
        default=None,
        description="页脚最底部自定义 HTML（备案号等）；NULL 表示不显示",
    )
    # 新注册用户权限默认值（公开可读：注册页据此提示，申请页据此展示入口）
    registration_default_can_publish: bool = True
    registration_default_can_accept: bool = True
    publish_permission_apply_enabled: bool = True
    # 全站发单权限总开关（公开可读：申请页/发单入口据此提示）
    publish_permission_required: bool = True
    updated_by: int | None
    updated_at: datetime
    logo_recommendation: str = "建议使用 512×512 以上的 PNG、JPEG 或 WebP 图片，文件不超过 10MB。"

    @field_validator("order_title_replacements", mode="before")
    @classmethod
    def fill_default_replacements(cls, value):
        return normalize_rules(value)

    @field_serializer("updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)

    model_config = ConfigDict(from_attributes=True)
