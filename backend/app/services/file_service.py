"""Shared safe image upload validation and storage helpers."""

from io import BytesIO
from pathlib import Path
from uuid import uuid4
import warnings

import anyio
from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.core.config import settings

_ALLOWED_TYPES = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/webp": {".webp"},
}
_CANONICAL_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_FORMAT_INFO = {
    "PNG": (".png", "image/png"),
    "JPEG": (".jpg", "image/jpeg"),
    "WEBP": (".webp", "image/webp"),
}
_MAX_IMAGE_PIXELS = 100_000_000
# 超过 2MB 的上传图自动重编码：最长边 2000px、JPEG 质量 82。
# 手机/游戏全屏截图动辄 5-10MB，跨境链路几十 KB/s 时要下载几分钟；
# 压后几百 KB 秒开，屏幕上看不出差别（老板已验收）。
_COMPRESS_THRESHOLD_BYTES = 2 * 1024 * 1024
_MAX_UPLOAD_EDGE = 2000
_UPLOAD_JPEG_QUALITY = 82


async def validate_image_upload(
    file: UploadFile,
    *,
    max_size_bytes: int = 10 * 1024 * 1024,
) -> tuple[bytes, str, str]:
    """Decode, validate, and normalize an uploaded image."""
    content_type = (file.content_type or "").lower()
    suffix = Path(file.filename or "").suffix.lower()
    data = await file.read(max_size_bytes + 1)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传图片不能为空")
    if len(data) > max_size_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"图片大小不能超过{max_size_bytes // (1024 * 1024)}MB")

    # PIL 解码/重编码 10MB 截图要 1-5 秒 CPU：放线程池执行，单 worker 事件循环
    # 冻结期间全站请求都会停摆，绝不能在事件循环里做。
    return await anyio.to_thread.run_sync(
        _validate_and_normalize_image, data, content_type, suffix
    )


def _validate_and_normalize_image(data: bytes, content_type: str, suffix: str) -> tuple[bytes, str, str]:
    declared_matches = content_type in _ALLOWED_TYPES
    extension_matches = declared_matches and suffix in _ALLOWED_TYPES[content_type]
    # 大图一律走重编码路径（即使声明的格式完全匹配），小图才允许原样透传
    must_compress = len(data) > _COMPRESS_THRESHOLD_BYTES
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > _MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片像素量过大")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image.verify()
            actual_format = image.format
    except HTTPException:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片内容无效")

    standard_info = _FORMAT_INFO.get(actual_format)
    if standard_info and declared_matches and extension_matches and content_type == standard_info[1] and not must_compress:
        return data, standard_info[0], standard_info[1]

    try:
        with Image.open(BytesIO(data)) as image:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image.load()
            if image.width * image.height > _MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片像素量过大")
            if max(image.width, image.height) > _MAX_UPLOAD_EDGE:
                scale = _MAX_UPLOAD_EDGE / max(image.width, image.height)
                image = image.resize(
                    (round(image.width * scale), round(image.height * scale)),
                    Image.LANCZOS,
                )
            if image.mode not in ("RGB", "L"):
                if "transparency" in image.info or "A" in image.getbands():
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=_UPLOAD_JPEG_QUALITY, optimize=True)
            return output.getvalue(), ".jpg", "image/jpeg"
    except HTTPException:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片内容无效")


def save_image_bytes(data: bytes, suffix: str, subdirectory: str = "") -> str:
    """Save already validated image bytes and return a relative URL."""
    relative_dir = Path(subdirectory)
    if relative_dir.is_absolute() or ".." in relative_dir.parts:
        raise ValueError("invalid upload subdirectory")
    target_dir = Path(settings.UPLOAD_DIR) / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{suffix}"
    (target_dir / filename).write_bytes(data)
    url_path = "/uploads/" + "/".join(part for part in relative_dir.parts if part) if relative_dir.parts else "/uploads"
    return f"{url_path}/{filename}"


async def save_image_upload(
    file: UploadFile,
    subdirectory: str = "",
    *,
    max_size_bytes: int = 10 * 1024 * 1024,
) -> str:
    """Validate and save an image, returning a relative /uploads URL."""
    data, suffix, _ = await validate_image_upload(file, max_size_bytes=max_size_bytes)
    return save_image_bytes(data, suffix, subdirectory)
