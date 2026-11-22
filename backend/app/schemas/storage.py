"""对象存储（腾讯云 COS）后台接口的请求/响应模型。

安全口径：SecretId / SecretKey 永不以明文下发。响应里只有**掩码**字段
（``AKID****Cj`` / ``****EJ``）和 ``cos_configured`` 布尔值；前端提交时，
把掩码原样回传（或使用 ``__KEEP__`` 哨兵、省略字段）都表示「保持原密钥」。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 前端保存其他字段时，用这个哨兵告诉后端「密钥不要改」。
# 直接回传响应里的掩码字符串同样会被识别为保持原值（见 is_keep_sentinel）。
SECRET_KEEP_SENTINEL = "__KEEP__"


def mask_secret_id(value: str | None) -> str | None:
    """SecretId 掩码：保留前 4 位与后 2 位，例如 ``AKID****Cj``。"""
    if not value:
        return None
    if len(value) <= 6:
        # 太短的值全掩掉，避免掩码本身泄露可用的前缀
        return "****"
    return f"{value[:4]}****{value[-2:]}"


def mask_secret_key(value: str | None) -> str | None:
    """SecretKey 掩码：只保留后 2 位，例如 ``****EJ``。"""
    if not value:
        return None
    if len(value) <= 2:
        return "****"
    return f"****{value[-2:]}"


def is_keep_sentinel(value: str | None) -> bool:
    """判断提交的密钥字段是否表示「保持原值」。

    None、空串、``__KEEP__`` 以及响应里的掩码（不含 ``****`` 以外的明文）
    都按保持处理；只有真正的新明文才会覆盖已存的密文。
    """
    if value is None:
        return True
    text = value.strip()
    if not text:
        return True
    if text == SECRET_KEEP_SENTINEL:
        return True
    # 前端把 GET 返回的掩码原样回传：任何包含掩码分隔符的值都不可能是真密钥
    return "****" in text


def _normalize_optional_text(value):
    """空串 / 纯空白 → None，其余去首尾空白。"""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    return text or None


class StorageSettingsUpdate(BaseModel):
    """保存对象存储配置。密钥字段为明文输入，落库前由端点加密。"""

    storage_mode: Literal["LOCAL", "COS"]
    cos_secret_id: str | None = None
    cos_secret_key: str | None = None
    cos_bucket: str | None = None
    cos_region: str | None = None
    cos_cdn_domain: str | None = None

    # 只允许这几个字段，避免前端拼错字段被静默吞掉
    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "cos_secret_id",
        "cos_secret_key",
        "cos_bucket",
        "cos_region",
        "cos_cdn_domain",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value):
        return _normalize_optional_text(value)


class StorageSettingsResponse(BaseModel):
    """后台看到的对象存储配置：只有掩码，绝无明文密钥。"""

    storage_mode: Literal["LOCAL", "COS"]
    cos_bucket: str | None = None
    cos_region: str | None = None
    cos_cdn_domain: str | None = None
    # NEVER return secrets in plaintext:
    cos_secret_id_masked: str | None = None
    cos_secret_key_masked: str | None = None
    # SecretId / SecretKey / Bucket / Region 四项齐备才算「已配置」
    cos_configured: bool = False

    @field_validator(
        "cos_bucket",
        "cos_region",
        "cos_cdn_domain",
        "cos_secret_id_masked",
        "cos_secret_key_masked",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value):
        return _normalize_optional_text(value)


class StorageTestRequest(BaseModel):
    """连接测试：允许测试尚未保存的凭据。

    四个字段任一为 None（或空串）时，对应值回落到已保存的配置；这样前端
    既可以在保存前「先测一把」，也可以在已配置好之后直接点测试。
    """

    cos_secret_id: str | None = None
    cos_secret_key: str | None = None
    cos_bucket: str | None = None
    cos_region: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "cos_secret_id",
        "cos_secret_key",
        "cos_bucket",
        "cos_region",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value):
        return _normalize_optional_text(value)


class StorageTestResponse(BaseModel):
    """连接测试结果。message 为面向管理员的中文说明。"""

    ok: bool
    message: str


class MigrationStatusResponse(BaseModel):
    """本地文件 → COS 迁移进度。

    ``pending`` 只统计尚未迁移的行；``failed`` 包含已达到次数上限、需要
    人工处理的坏文件。``local_files`` / ``local_bytes`` 是磁盘上仍然存在的
    文件数与字节数（清理完成后应归零）。
    """

    total: int
    done: int
    failed: int
    pending: int
    running: bool
    paused: bool
    current_path: str | None = None
    last_error: str | None = None
    local_files: int
    local_bytes: int

    @field_validator("current_path", "last_error", mode="before")
    @classmethod
    def normalize_text(cls, value):
        return _normalize_optional_text(value)


class StorageCleanupStatusResponse(BaseModel):
    """清理本地残留文件的进度。"""

    running: bool
    deleted: int
    failed: int
    remaining: int
    last_error: str | None = None

    @field_validator("last_error", mode="before")
    @classmethod
    def normalize_text(cls, value):
        return _normalize_optional_text(value)
