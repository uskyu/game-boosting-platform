"""Platform-wide site settings (a single logical row)."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
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
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=func.now(), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updater: Mapped["User | None"] = relationship("User", foreign_keys=[updated_by], lazy="noload")
