"""提现渠道开关（单行，id=1）。

管理员在后台控制每种提现渠道是否对用户开放：

- ``alipay_enabled``：是否开放支付宝提现。
- ``wechat_enabled``：是否开放微信提现。

默认两种渠道都开放。关闭某渠道后：前端不再显示该选项，后端对新申请
校验渠道开关（进行中/已发放的旧提现不受影响）。

开关修改即时生效、不烙盘：每次请求现读设置现算。
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base


class WithdrawalChannelSetting(Base):
    """提现渠道开关设置（单行，id=1）。"""

    __tablename__ = "withdrawal_channel_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False, default=1)

    # 是否开放支付宝提现。默认开放：管理员如需关闭保存一次即可。
    alipay_enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default=sa.text("1"),
    )

    # 是否开放微信提现。默认开放。
    wechat_enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default=sa.text("1"),
    )

    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    updater: Mapped["User | None"] = relationship(
        "User", foreign_keys=[updated_by], lazy="noload"
    )

    def __repr__(self) -> str:
        return (
            f"<WithdrawalChannelSetting id={self.id} "
            f"alipay_enabled={self.alipay_enabled} "
            f"wechat_enabled={self.wechat_enabled}>"
        )
