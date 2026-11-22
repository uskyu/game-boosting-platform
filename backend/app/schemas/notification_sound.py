"""通知提示音配置的请求/响应模型与归一化工具。

四个固定槽位分别对应前端的来单、被接手、申请取消、聊天消息提示音。数据库
里只存一条 JSON（``site_settings.notification_sounds``），``url=None`` 表示
该槽位使用内置默认音频（静态 WAV 或 WebAudio 合成音）。

归一化（``normalize_sounds``）是前后端共同的契约：公开设置接口、后台读取、
保存返回值都必须**始终返回四个槽位**，缺失槽位填默认值、未知键丢弃，这样
前端不必处理「半份配置」。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SOUND_SLOTS = ("new_order", "claimed", "cancel_requested", "chat_message")

# 自定义音频必须落在后端存储的 sounds/ 目录下，前端把它当站内相对 URL 播放。
SOUND_URL_PREFIX = "/uploads/sounds/"
DEFAULT_VOLUME = 80


def _default_slot() -> dict[str, Any]:
    return {"url": None, "volume": DEFAULT_VOLUME, "enabled": True}


def default_sounds() -> dict[str, dict[str, Any]]:
    """四个槽位的完整默认配置（全部使用内置音频、音量 80、未静音）。"""
    return {slot: _default_slot() for slot in SOUND_SLOTS}


class NotificationSoundSlot(BaseModel):
    """单个通知槽位的配置。"""

    url: str | None = Field(
        default=None,
        description="自定义音频地址（/uploads/sounds/ 开头）；None=使用内置默认音频",
    )
    volume: int = Field(default=DEFAULT_VOLUME, ge=0, le=100, description="音量百分比 0-100")
    enabled: bool = Field(default=True, description="false=该提示音静音")

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if not text.startswith(SOUND_URL_PREFIX):
            raise ValueError(f"音频地址必须以 {SOUND_URL_PREFIX} 开头")
        return text


class NotificationSoundsUpdate(BaseModel):
    """后台保存请求：只提交需要修改的槽位（键必须是已知槽位）。"""

    sounds: dict[str, NotificationSoundSlot]

    model_config = ConfigDict(extra="forbid")

    @field_validator("sounds")
    @classmethod
    def validate_slots(
        cls, value: dict[str, NotificationSoundSlot]
    ) -> dict[str, NotificationSoundSlot]:
        # 空对象是合法的「不改任何槽位」请求；未知键必须拒绝
        unknown = [key for key in value if key not in SOUND_SLOTS]
        if unknown:
            raise ValueError(f"未知的通知音频槽位：{', '.join(unknown)}")
        return value


class NotificationSoundsResponse(BaseModel):
    """后台读取/保存的返回：始终包含全部四个槽位。"""

    sounds: dict[str, NotificationSoundSlot]


class NotificationSoundResetRequest(BaseModel):
    """把单个槽位的自定义音频恢复为内置默认（音量/静音状态保留）。"""

    slot: str

    @field_validator("slot")
    @classmethod
    def validate_slot(cls, value: str) -> str:
        if value not in SOUND_SLOTS:
            raise ValueError(f"未知的通知音频槽位：{value}")
        return value


def _coerce_slot(raw: Any) -> dict[str, Any]:
    """把任意来源（DB JSON / 旧配置 / 脏数据）的单槽位值收敛为合法配置。"""
    slot = _default_slot()
    if not isinstance(raw, dict):
        return slot

    url = raw.get("url")
    if isinstance(url, str):
        text = url.strip()
        if text.startswith(SOUND_URL_PREFIX):
            slot["url"] = text

    volume = raw.get("volume", DEFAULT_VOLUME)
    try:
        number = int(volume)
    except (TypeError, ValueError):
        number = DEFAULT_VOLUME
    slot["volume"] = max(0, min(100, number))

    enabled = raw.get("enabled", True)
    slot["enabled"] = enabled if isinstance(enabled, bool) else True
    return slot


def normalize_sounds(raw: dict | None) -> dict[str, dict[str, Any]]:
    """归一化通知音频配置：补齐缺失槽位、丢弃未知键、夹紧音量范围。

    ``raw`` 为 None / 非 dict 时返回全默认配置（对应数据库 NULL）。
    """
    if not isinstance(raw, dict):
        return default_sounds()

    normalized = default_sounds()
    for slot_key in SOUND_SLOTS:
        if slot_key in raw:
            normalized[slot_key] = _coerce_slot(raw[slot_key])
    return normalized
