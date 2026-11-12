"""Platform-wide site settings (a single logical row)."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class SiteSetting(Base):
    """The platform's public branding and description settings."""

    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    site_name: Mapped[str] = mapped_column(String(200), nullable=False, default="游戏服务平台")
    site_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    site_logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    favicon_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 大厅「今日已接单」区块全站开关：默认开，管理员可在站点设置里整体关闭
    hall_recent_claims_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )
    # 安全设置（2026-10-22）：两个验证码开关 + 同时在线设备上限。
    # 放在站点设置里是为了复用现有单行配置与后台页面；公开可读，前端据此
    # 决定注册页是否渲染验证码、接单弹窗是否提示设备上限。
    registration_captcha_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )
    accept_captcha_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default="0"
    )
    # 0 = 不限制；正数 = 同一账号最多同时登录的设备数，超限挤掉最早登录的
    max_online_devices: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=2, server_default="2"
    )
    # 订单标题关键词替换规则（2026-11-02）：JSON 数组，元素
    # {"original": "名牌", "replacement": "【MP】"}；NULL=默认规则。
    # 发单/改单时由 order_service 按此规则替换标题，聊天订单卡片同步。
    order_title_replacements: Mapped[list[dict] | None] = mapped_column(
        JSON, nullable=True
    )
    # 页脚底部自定义 HTML（2026-11-06）：主要用于展示 ICP 备案号与备案链接。
    # 由管理员在站点设置里填写，前台渲染在整页最底部；NULL/空串 = 不显示。
    footer_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 单账号权限默认值（2026-11-07）：一套代码服务多站。生产站保持默认允许
    # 发单/接单；新站首次部署可通过 env 把发单默认关掉，再让用户自助申请、
    # 管理员审核。后台修改只影响之后注册的新用户，不回溯老用户。
    registration_default_can_publish: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )
    registration_default_can_accept: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )
    # 是否允许无发单权限的用户自助提交申请；关闭后只能由管理员直接开通。
    publish_permission_apply_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )
    # 全站发单权限总开关（2026-11-12）：true=非管理员必须已开通发单权限
    # （can_publish=true 且 publish_banned=false）才能发单；false=全站开放，
    # 未被封禁发单的非管理员都可发单。管理员始终不受此开关限制。
    publish_permission_required: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default="1"
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updater: Mapped["User | None"] = relationship("User", foreign_keys=[updated_by], lazy="noload")
