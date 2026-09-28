"""提现渠道开关（单行读取，缺失时按默认「全部开放」创建）。

管理员在后台控制每种提现渠道是否对用户开放：

- ``alipay_enabled``：是否开放支付宝提现。
- ``wechat_enabled``：是否开放微信提现。

默认两种渠道都开放。关闭某渠道后：前端不再显示该选项（get_channel_options），
后端的 create_withdrawal 会用 ensure_channel_enabled 拦下新增申请
（进行中/已发放的旧提现不受影响）。

开关修改即时生效、不烙盘：每次请求现读设置现算。

注意：本模块绝不 import wallet_service（wallet_service 反过来依赖本模块做
渠道校验），否则会形成循环导入。
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.withdrawal_channel_settings import WithdrawalChannelSetting

__all__ = [
    "CHANNEL_LABELS",
    "ensure_channel_enabled",
    "get_channel_options",
    "get_or_create_withdrawal_channel_settings",
]

# 前端选项与错误文案用的中文标签。
CHANNEL_LABELS = {
    "ALIPAY": "支付宝",
    "WECHAT": "微信",
    "BANK": "银行卡",
}

# 仅这两种渠道受后台开关控制；BANK 与未知渠道不在校验范围内。
_SWITCHABLE_CHANNELS = ("ALIPAY", "WECHAT")


async def get_or_create_withdrawal_channel_settings(
    db: AsyncSession,
) -> WithdrawalChannelSetting:
    """读取提现渠道开关（单行，id=1）；缺失时按默认「全部开放」创建。"""
    result = await db.execute(
        select(WithdrawalChannelSetting).where(WithdrawalChannelSetting.id == 1)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = WithdrawalChannelSetting(
            id=1,
            alipay_enabled=True,
            wechat_enabled=True,
        )
        db.add(setting)
        await db.flush()
        await db.refresh(setting)
    return setting


async def ensure_channel_enabled(db: AsyncSession, channel: str) -> None:
    """渠道被后台关闭时拒绝新增提现（400）。

    ``channel`` 接受 WithdrawalChannel 枚举或其字符串值（str-enum 与字符串
    的比较/查表等价）。BANK 不在开关控制内、未知渠道同样放行，二者直接返回。
    """
    normalized = getattr(channel, "value", channel)
    if normalized not in _SWITCHABLE_CHANNELS:
        return
    setting = await get_or_create_withdrawal_channel_settings(db)
    enabled = (
        setting.alipay_enabled if normalized == "ALIPAY" else setting.wechat_enabled
    )
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{CHANNEL_LABELS[normalized]}提现暂未开放，请稍后再试",
        )


async def get_channel_options(db: AsyncSession) -> list[dict]:
    """前端提现表单的渠道选项列表（含各渠道是否开放）。"""
    setting = await get_or_create_withdrawal_channel_settings(db)
    return [
        {
            "channel": "ALIPAY",
            "label": CHANNEL_LABELS["ALIPAY"],
            "enabled": bool(setting.alipay_enabled),
        },
        {
            "channel": "WECHAT",
            "label": CHANNEL_LABELS["WECHAT"],
            "enabled": bool(setting.wechat_enabled),
        },
        # BANK 不在后台开关控制之内，始终开放。
        {
            "channel": "BANK",
            "label": CHANNEL_LABELS["BANK"],
            "enabled": True,
        },
    ]
