# ROUTER_INCLUDE_HINT (for app/api/router.py — NOT edited by this module's author)
#
#   from app.api.endpoints.admin_sounds import router as admin_sounds_router
#   api_router.include_router(admin_sounds_router)
#
# The include line is order-independent (prefix /admin/sounds does not collide
# with any existing route). Only the two lines above are needed.

"""管理员：自定义通知提示音（四个固定槽位）。

路由前缀 ``/admin/sounds``，全部要求 ``get_current_admin``：

* ``GET  ""``       读取归一化后的四槽位配置（NULL 列 = 全部内置默认）
* ``PUT  ""``       保存部分/全部槽位（未提交的槽位保持原值）
* ``POST "/upload"`` multipart：``slot`` + ``file``（MP3/WAV，≤2MB）
* ``POST "/reset"`` 把某槽位的自定义音频恢复内置默认（音量/静音保留）

持久化写法：SQLAlchemy 的 JSON 列不支持原地修改检测，所有写入都构造**新的
整份 dict** 再赋值。写成功后登记 post-commit 广播 ``notification_sounds_changed``
（无业务数据），在线客户端收到后重拉公开站点设置即可拿到新配置。
"""

from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import DatabaseSession, get_current_admin
from app.core.config import settings
from app.core.post_commit import run_after_commit
from app.models.user import User
from app.schemas.notification_sound import (
    SOUND_SLOTS,
    NotificationSoundResetRequest,
    NotificationSoundsResponse,
    NotificationSoundsUpdate,
    normalize_sounds,
)
from app.services.site_setting_service import get_notification_sounds, get_or_create_site_setting
from app.services.connection_manager import get_connection_manager

router = APIRouter(prefix="/admin/sounds", tags=["admin-sounds"])

# 音频大小上限：提示音最长几秒，2MB 对 320kbps MP3 已经够用；超出拒绝，
# 避免把后台存储当文件网盘用。
_MAX_AUDIO_BYTES = 2 * 1024 * 1024

# 允许的组合：content_type -> 允许的扩展名。声明类型与扩展名必须同时命中。
_AUDIO_TYPES: dict[str, set[str]] = {
    "audio/mpeg": {".mp3"},
    "audio/wav": {".wav"},
    "audio/x-wav": {".wav"},
}
_SUFFIX_BY_TYPE = {"audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav"}


async def _persist_audio(data: bytes, key: str, content_type: str, db) -> str:
    """把音频字节写入当前存储后端并返回站内相对 URL。

    有意保留的本地兜底：storage_service 由并行改动引入（COS 上传/本地磁盘
    分流都在其中），若该模块尚未落地（ImportError）或还没有 ``save_bytes``
    （AttributeError），这里直接写 ``UPLOAD_DIR/key``，保证提示音功能不被
    存储层进度阻塞。存储层就绪后 ``save_bytes`` 会自动接管：COS 模式下对象
    上传到桶里，``/uploads/`` 回源中间件仍按同一 URL 读取。
    """
    try:
        from app.services import storage_service  # 由存储层改动提供

        await storage_service.save_bytes(data, key, content_type, db)
    except (ImportError, AttributeError):
        target = Path(settings.UPLOAD_DIR) / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return "/uploads/" + key


async def _broadcast_sounds_changed() -> None:
    """事务提交后通知在线客户端重拉公开站点设置（事件本身不带数据）。"""
    connection_manager = get_connection_manager()
    payload = {"event": "notification_sounds_changed", "data": {}}

    async def _broadcast() -> None:
        await connection_manager.broadcast(payload)

    await run_after_commit(_broadcast)


async def _save_sounds(db, admin: User, sounds: dict) -> dict:
    """整份替换 JSON 列并 flush/refresh，返回归一化后的四槽位配置。"""
    setting = await get_or_create_site_setting(db)
    # JSON 列必须整对象赋值（原地修改不会被变更追踪），这里拿到的已经是新 dict
    setting.notification_sounds = sounds
    setting.updated_by = admin.id
    await db.flush()
    await db.refresh(setting)
    await _broadcast_sounds_changed()
    return normalize_sounds(setting.notification_sounds)


def _validate_audio(file: UploadFile, data: bytes) -> tuple[str, str]:
    """校验音频类型/大小，返回 (规范化扩展名, content_type)。"""
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    suffix = Path(file.filename or "").suffix.lower()
    allowed_suffixes = _AUDIO_TYPES.get(content_type)
    if not allowed_suffixes or suffix not in allowed_suffixes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 MP3 或 WAV 格式的音频文件",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="上传音频不能为空"
        )
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"音频大小不能超过{_MAX_AUDIO_BYTES // (1024 * 1024)}MB",
        )
    return _SUFFIX_BY_TYPE[content_type], content_type


@router.get("", response_model=NotificationSoundsResponse, summary="读取通知音频配置")
async def get_admin_notification_sounds(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> NotificationSoundsResponse:
    sounds = await get_notification_sounds(db)
    return NotificationSoundsResponse(sounds=sounds)


@router.put("", response_model=NotificationSoundsResponse, summary="保存通知音频配置")
async def update_admin_notification_sounds(
    payload: NotificationSoundsUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> NotificationSoundsResponse:
    # 合并语义：未提交的槽位沿用当前值；已提交槽位里「未显式出现的字段」也
    # 沿用当前值（只改了音量的一次保存不会意外清掉已上传的自定义 URL）。
    # 显式传 url=null 才会清空（等价于该槽位恢复内置）。
    merged = await get_notification_sounds(db)
    for slot_key, slot_value in payload.sounds.items():
        patch = slot_value.model_dump(exclude_unset=True)
        merged[slot_key] = {**merged[slot_key], **patch}
    sounds = await _save_sounds(db, current_admin, merged)
    return NotificationSoundsResponse(sounds=sounds)


@router.post("/upload", summary="上传通知音频（MP3/WAV，≤2MB）")
async def upload_notification_sound(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    slot: str = Form(..., description="通知槽位：new_order/claimed/cancel_requested/chat_message"),
    file: UploadFile = File(..., description="音频文件（MP3 或 WAV）"),
) -> dict:
    if slot not in SOUND_SLOTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未知的通知音频槽位：{slot}",
        )
    data = await file.read(_MAX_AUDIO_BYTES + 1)
    suffix, content_type = _validate_audio(file, data)

    filename = f"{slot}-{uuid4().hex[:8]}{suffix}"
    key = f"sounds/{filename}"
    await _persist_audio(data, key, content_type, db)

    # 上传即生效：该槽位指向新文件，音量/静音状态沿用当前值
    merged = await get_notification_sounds(db)
    merged[slot] = {
        "url": "/uploads/" + key,
        "volume": merged[slot]["volume"],
        "enabled": merged[slot]["enabled"],
    }
    sounds = await _save_sounds(db, current_admin, merged)
    return {"url": "/uploads/" + key, "sounds": sounds}


@router.post("/reset", response_model=NotificationSoundsResponse, summary="恢复某槽位的内置音频")
async def reset_notification_sound(
    payload: NotificationSoundResetRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> NotificationSoundsResponse:
    merged = await get_notification_sounds(db)
    slot = merged[payload.slot]
    # 只清空自定义 URL；管理员调好的音量与静音开关保留
    merged[payload.slot] = {
        "url": None,
        "volume": slot["volume"],
        "enabled": slot["enabled"],
    }
    sounds = await _save_sounds(db, current_admin, merged)
    return NotificationSoundsResponse(sounds=sounds)
