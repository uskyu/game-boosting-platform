"""Shared safe image upload validation and storage helpers."""

from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings

_ALLOWED_TYPES = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/webp": {".webp"},
}
_CANONICAL_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_MAGIC = {
    ".png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
    ".jpg": lambda data: data.startswith(b"\xff\xd8\xff"),
    ".webp": lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
}


async def validate_image_upload(
    file: UploadFile,
    *,
    max_size_bytes: int = 5 * 1024 * 1024,
) -> tuple[bytes, str]:
    """Validate an image's declared type, extension, signature and size."""
    content_type = (file.content_type or "").lower()
    suffix = Path(file.filename or "").suffix.lower()
    allowed_suffixes = _ALLOWED_TYPES.get(content_type)
    if allowed_suffixes is None or suffix not in {item for values in _ALLOWED_TYPES.values() for item in values}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持 png、jpeg、webp 图片")
    if suffix and suffix not in allowed_suffixes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片扩展名与 MIME 类型不匹配")
    expected_suffix = _CANONICAL_SUFFIX[content_type]

    data = await file.read(max_size_bytes + 1)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传图片不能为空")
    if len(data) > max_size_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"图片大小不能超过{max_size_bytes // (1024 * 1024)}MB")
    if not _MAGIC[expected_suffix](data):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片内容无效")
    return data, expected_suffix


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
    max_size_bytes: int = 5 * 1024 * 1024,
) -> str:
    """Validate and save an image, returning a relative /uploads URL."""
    data, suffix = await validate_image_upload(file, max_size_bytes=max_size_bytes)
    return save_image_bytes(data, suffix, subdirectory)
