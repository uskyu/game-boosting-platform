"""对象存储门面：本地磁盘 / 腾讯云 COS，按站点设置实时选择。

上传的 URL 契约保持不变：数据库里始终存 ``/uploads/<key>`` 这类相对路径，
本模块只负责把同一个 key 落到当前生效的后端（本地磁盘或 COS）。因此切换
存储后端不需要迁移任何历史 URL，也不会改变前端/API 的响应格式。

设计要点：
- COS SDK 是**同步阻塞**的（requests + urllib3）。本进程只跑 1 个 uvicorn
  worker，任何在事件循环里直接调用 SDK 的写法都会让全站请求一起卡住，
  所以所有 SDK 调用（put/get/head/delete）一律经 ``anyio.to_thread.run_sync``
  丢进线程池。
- 配置读取带 2 秒进程内缓存，与 ``get_hall_sort_mode`` 同样的短 TTL：上传
  不是高频路径，2 秒足以避免每次上传都回表，又能在后台切换后近乎立即生效。
- 任何配置解密/解析失败都退回 LOCAL，绝不让「后台填错密钥」把上传链路打挂。
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.security import decrypt_text
from app.models.site_setting import SiteSetting

logger = logging.getLogger(__name__)

# 配置刷新单飞锁：缓存刚过期时把并发回表收敛成一次。注意不能事件循环外使用。
_REFRESH_LOCK = anyio.Lock()

# site_settings 里与对象存储相关的列（迁移 076_object_storage 定义，命名
# 与后台端点/模型一致）。这里集中列出，便于和迁移对照检查。
_STORAGE_COLUMNS = (
    "storage_mode",
    "cos_secret_id",
    "cos_secret_key",
    "cos_bucket",
    "cos_region",
    "cos_cdn_domain",
)

_UPLOAD_URL_PREFIX = "/uploads/"
_CONFIG_CACHE_TTL_SECONDS = 2.0
_LOCAL_MODE = "LOCAL"
_COS_MODE = "COS"
# COS 单次请求超时（秒）。远小于 SDK 默认的 30s，避免故障时线程池被占满。
_COS_TIMEOUT_SECONDS = 15

# 进程内配置缓存（独立于大厅排序缓存）。value=None 表示尚未读取。
_CONFIG_CACHE: dict[str, Any] = {"value": None, "expires_at": 0.0}
# CosS3Client 按 (bucket, region, secret_id, timeout) 缓存；client 内部自带
# requests 连接池，重建会丢掉 keep-alive，上传/回源都会明显变慢。
_CLIENT_CACHE: dict[tuple[str, str, str, float], Any] = {}


class StorageError(Exception):
    """存储层错误：对上层暴露中文可读信息，绝不透出密钥或签名。"""


@dataclass(frozen=True)
class StorageConfig:
    """当前生效的存储配置（密钥已解密；只在进程内使用，禁止外传/日志）。"""

    mode: str  # "LOCAL" | "COS"
    secret_id: str | None = None
    secret_key: str | None = None
    bucket: str | None = None
    region: str | None = None
    cdn_domain: str | None = None


_LOCAL_SETTING = StorageConfig(mode=_LOCAL_MODE)


def invalidate_storage_config_cache() -> None:
    """让存储配置缓存与 COS client 缓存失效。

    后台保存存储设置后调用即可在同一进程内立即生效，无需等 2 秒 TTL。
    """
    _CONFIG_CACHE["value"] = None
    _CONFIG_CACHE["expires_at"] = 0.0
    _CLIENT_CACHE.clear()


# =============================================================================
# 路径 / key 规范化
# =============================================================================


def normalize_key(key: str) -> str:
    """规范化对象 key：相对 POSIX 路径、无前导斜杠、拒绝路径穿越。

    合法样例：``orders/abc.jpg``、``chat/def.png``、``booster-proof-1-abc.jpg``。
    """
    if not isinstance(key, str):
        raise ValueError("invalid storage key")
    value = key.strip().replace("\\", "/")
    if not value or "\x00" in value:
        raise ValueError("invalid storage key")
    if value.startswith("/"):
        raise ValueError("invalid storage key")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("invalid storage key")
    # Windows 盘符或冒号在首段一律视为非法（防止拼出绝对路径）。
    if ":" in parts[0]:
        raise ValueError("invalid storage key")
    return "/".join(parts)


def key_from_url(url: str) -> str:
    """把 ``/uploads/orders/x.jpg`` 转成对象 key ``orders/x.jpg``。

    拒绝路径穿越与绝对路径；不接受非 /uploads 开头的输入，避免调用方拿
    任意字符串去探测磁盘。
    """
    if not isinstance(url, str):
        raise ValueError("invalid upload url")
    value = url.strip()
    if value == "/uploads" or value == _UPLOAD_URL_PREFIX:
        raise ValueError("invalid upload url")
    if not value.startswith(_UPLOAD_URL_PREFIX):
        raise ValueError("invalid upload url")
    return normalize_key(value[len(_UPLOAD_URL_PREFIX):])


def guess_content_type(key: str) -> str:
    """按后缀猜 content-type；未知类型返回 application/octet-stream。"""
    guessed, _ = mimetypes.guess_type(key)
    return guessed or "application/octet-stream"


def _local_path(key: str) -> Path:
    """key -> UPLOAD_DIR 下的本地路径（key 已规范化，不含 ..）。"""
    return Path(settings.UPLOAD_DIR).joinpath(*normalize_key(key).split("/"))


# =============================================================================
# 配置读取
# =============================================================================


async def _load_storage_setting_row(db) -> dict[str, Any]:
    """读取 site_settings id=1 的存储相关列；任何失败都返回空 dict。

    用 ORM 单条标量查询（与 ``get_hall_sort_mode`` 同一模式），只取需要的
    列，不做整行加载。迁移在应用启动前执行，因此列已存在；若读取失败
    （表/列未就绪、DB 不可用），调用方会退回 LOCAL，上传链路不受影响。
    """
    if db is None:
        return {}
    columns = [
        getattr(SiteSetting, column)
        for column in _STORAGE_COLUMNS
        if hasattr(SiteSetting, column)
    ]
    if not columns:
        return {}
    try:
        row = (
            await db.execute(select(*columns).where(SiteSetting.id == 1))
        ).mappings().first()
    except SQLAlchemyError:
        logger.debug("Storage settings row lookup failed", exc_info=True)
        return {}
    if row is None:
        return {}
    return {column: row[column] for column in row.keys()}


def _decrypt_secret(value: Any) -> str | None:
    """解密后台保存的密钥；空值/格式错误一律返回 None（触发 LOCAL 兜底）。"""
    if value is None:
        return None
    try:
        return decrypt_text(str(value))
    except Exception:  # noqa: BLE001 - 解密失败不得影响上传路径
        logger.debug("COS secret decrypt failed", exc_info=True)
        return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _clean_cdn_domain(value: Any) -> str | None:
    """把后台填的 CDN 域名归一成 host（容忍带 scheme / 结尾斜杠）。"""
    raw = _clean(value)
    if not raw:
        return None
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0].strip()
    return raw or None


async def _build_storage_config(db) -> StorageConfig:
    """按 DB 行构造存储配置；任何异常/缺项都退回 LOCAL。"""
    try:
        row = await _load_storage_setting_row(db)
    except Exception:  # noqa: BLE001 - 配置读取失败必须退回本地磁盘
        logger.debug("Storage settings read failed", exc_info=True)
        return _LOCAL_SETTING

    mode = _clean(row.get("storage_mode")) or _LOCAL_MODE
    mode = mode.upper()
    if mode != _COS_MODE:
        return _LOCAL_SETTING

    secret_id = _decrypt_secret(row.get("cos_secret_id"))
    secret_key = _decrypt_secret(row.get("cos_secret_key"))
    bucket = _clean(row.get("cos_bucket"))
    region = _clean(row.get("cos_region"))
    if not (secret_id and secret_key and bucket and region):
        # 后台选了 COS 但密钥/桶没填全（或解密失败）：宁可写本地磁盘，
        # 也不能让用户上传直接失败。
        logger.warning(
            "COS storage selected but configuration is incomplete; falling back to LOCAL"
        )
        return _LOCAL_SETTING

    return StorageConfig(
        mode=_COS_MODE,
        secret_id=secret_id,
        secret_key=secret_key,
        bucket=bucket,
        region=region,
        cdn_domain=_clean_cdn_domain(row.get("cos_cdn_domain")),
    )


async def get_storage_config(db) -> StorageConfig:
    """返回当前生效的存储配置（2 秒进程内缓存，模式切换近乎立即生效）。

    ``db`` 可以为 None（纯本地调用场景/DB 不可用兜底）；此时返回 LOCAL。
    并发请求在缓存刚过期时会同时判定为 miss；``_REFRESH_LOCK`` 把实际回表
    收敛成一次，其余请求拿到刚写入的结果，避免一秒钟内几十条连接被同一
    行配置占满。
    """
    now = time.monotonic()
    cached = _CONFIG_CACHE["value"]
    if isinstance(cached, StorageConfig) and now < _CONFIG_CACHE["expires_at"]:
        return cached

    async with _REFRESH_LOCK:
        # 二次检查：等锁期间另一个协程可能已经完成刷新。
        now = time.monotonic()
        cached = _CONFIG_CACHE["value"]
        if isinstance(cached, StorageConfig) and now < _CONFIG_CACHE["expires_at"]:
            return cached

        config = await _build_storage_config(db)
        _CONFIG_CACHE["value"] = config
        _CONFIG_CACHE["expires_at"] = now + _CONFIG_CACHE_TTL_SECONDS
        return config


async def get_cos_config(db) -> StorageConfig | None:
    """按已保存的 COS 凭据构造**显式 COS 配置**，不跟随 storage_mode。

    数据库备份必须落到对象存储：管理员即使把当前上传模式切回 LOCAL，备份
    也不该悄悄写到本地磁盘。只要 ``cos_secret_id`` / ``cos_secret_key`` /
    ``cos_bucket`` / ``cos_region`` 四项齐全且两个密钥都能解密，就返回
    ``mode="COS"`` 的配置；否则返回 None，由调用方给出「请先配置 COS」的
    中文错误。

    与 ``get_storage_config`` 的区别：不走 2 秒缓存（备份频率低，但要求
    读到刚保存的配置），也不做 LOCAL 兜底。密钥只留在进程内，绝不写日志。
    """
    try:
        row = await _load_storage_setting_row(db)
    except Exception:  # noqa: BLE001 - 配置读取失败等价于 COS 不可用
        logger.debug("COS backup config read failed", exc_info=True)
        return None

    secret_id = _decrypt_secret(row.get("cos_secret_id"))
    secret_key = _decrypt_secret(row.get("cos_secret_key"))
    bucket = _clean(row.get("cos_bucket"))
    region = _clean(row.get("cos_region"))
    if not (secret_id and secret_key and bucket and region):
        return None

    return StorageConfig(
        mode=_COS_MODE,
        secret_id=secret_id,
        secret_key=secret_key,
        bucket=bucket,
        region=region,
        cdn_domain=_clean_cdn_domain(row.get("cos_cdn_domain")),
    )


def peek_storage_config() -> StorageConfig | None:
    """读取（不刷新）尚未过期的缓存配置；未命中返回 None。

    供 /uploads 回源中间件使用：命中缓存时完全不碰数据库，图片并发再高
    也不会额外占用连接池。
    """
    now = time.monotonic()
    cached = _CONFIG_CACHE["value"]
    if isinstance(cached, StorageConfig) and now < _CONFIG_CACHE["expires_at"]:
        return cached
    return None


# =============================================================================
# COS client / 错误翻译
# =============================================================================


def _cos_sdk():
    """延迟导入 SDK：未安装时给出中文错误，而不是让应用启动失败。"""
    try:
        from qcloud_cos import CosConfig, CosS3Client
        from qcloud_cos.cos_exception import CosClientError, CosServiceError
    except ImportError as exc:  # pragma: no cover - requirements 已固定版本
        raise StorageError("对象存储 SDK 未安装，请联系管理员") from exc
    return CosConfig, CosS3Client, CosClientError, CosServiceError


def _cos_client(cfg: StorageConfig, timeout_seconds: float | None = None):
    """按 (bucket, region, secret_id, timeout) 懒缓存 CosS3Client。

    ``timeout_seconds`` 默认沿用图片上传的 15s 短超时；数据库备份等大文件
    上传可由调用方显式传更长超时（例如 300s）。缓存键包含超时值，避免同
    一组凭据下不同超时的 client 互相串用。
    """
    if cfg.mode != _COS_MODE:
        raise StorageError("当前存储模式不是 COS")
    if not (cfg.bucket and cfg.region and cfg.secret_id and cfg.secret_key):
        raise StorageError("COS 配置不完整")
    timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(_COS_TIMEOUT_SECONDS)
    )
    if timeout <= 0:
        timeout = float(_COS_TIMEOUT_SECONDS)
    cache_key = (cfg.bucket, cfg.region, cfg.secret_id, timeout)
    client = _CLIENT_CACHE.get(cache_key)
    if client is not None:
        return client
    CosConfig, CosS3Client, _, _ = _cos_sdk()
    client = CosS3Client(
        CosConfig(
            Region=cfg.region,
            SecretId=cfg.secret_id,
            SecretKey=cfg.secret_key,
            Scheme="https",
            # 有界超时：SDK 默认 30s，COS 抖动时会把线程池占满，进而拖慢
            # 所有上传；15s 足够大图单次上传，且失败能快速报错重试。
            # 数据库备份包可能几十 MB，调用方可显式传 300s。
            Timeout=timeout,
        )
    )
    _CLIENT_CACHE[cache_key] = client
    return client


_COS_ERROR_DETAILS: dict[str, str] = {
    "NoSuchBucket": "存储桶不存在，请检查 Bucket 名称",
    "NoSuchKey": "对象不存在",
    "AccessDenied": "访问被拒绝（密钥无效或无权访问该存储桶）",
    "Forbidden": "访问被拒绝（密钥无效或无权访问该存储桶）",
    "InvalidAccessKeyId": "SecretId 无效，请检查访问密钥",
    "InvalidSecretId": "SecretId 无效，请检查访问密钥",
    "SignatureDoesNotMatch": "签名校验失败，请检查 SecretKey",
    "RequestTimeTooSkewed": "请求时间与腾讯云服务器偏差过大，请检查服务器时间",
    "InvalidRequest": "请求参数无效，请检查存储配置",
    "AuthorizationHeaderMalformed": "鉴权信息格式错误，请检查存储配置",
    "BucketRegionError": "存储桶地域与配置不一致，请检查 Region",
    "IllegalLocationConstraintException": "存储桶地域与配置不一致，请检查 Region",
}


def _describe_cos_error(exc: BaseException) -> str:
    """把 COS/网络异常翻译成简短中文原因，绝不包含密钥或签名。"""
    try:
        from qcloud_cos.cos_exception import CosClientError, CosServiceError
    except ImportError:  # pragma: no cover
        return "对象存储请求失败，请稍后重试"

    if isinstance(exc, CosServiceError):
        code = ""
        try:
            code = str(exc.get_error_code() or "")
        except Exception:  # noqa: BLE001
            code = ""
        if code in _COS_ERROR_DETAILS:
            return _COS_ERROR_DETAILS[code]
        status = None
        try:
            status = exc.get_status_code()
        except Exception:  # noqa: BLE001
            status = None
        if status == 403:
            return "访问被拒绝（密钥无效或无权访问该存储桶）"
        if status == 404:
            return "存储桶或对象不存在"
        if status == 429:
            return "请求过于频繁，请稍后重试"
        if isinstance(status, int) and 500 <= status < 600:
            return "对象存储服务暂时不可用，请稍后重试"
        return f"对象存储请求失败（{code or status or '未知错误'}）"
    if isinstance(exc, CosClientError):
        return "无法连接对象存储（网络超时或被拒绝）"
    if isinstance(exc, StorageError):
        return str(exc)
    if isinstance(exc, OSError):
        return "本地磁盘读写失败"
    return "对象存储请求失败，请稍后重试"


class _ObjectMissing(Exception):
    """内部信号：COS 侧确认对象不存在（404），读取返回 None。"""


# =============================================================================
# 线程池内的同步实现（唯一允许直接触碰 SDK / 磁盘的地方）
# =============================================================================


def _local_write(key: str, data: bytes) -> None:
    path = _local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        os.chmod(path, 0o644)
    except OSError:  # Windows 上 chmod 基本无效，忽略即可
        pass


def _local_read(key: str) -> tuple[bytes, str] | None:
    path = _local_path(key)
    if not path.is_file():
        return None
    return path.read_bytes(), guess_content_type(key)


def _cos_put(client, bucket: str, key: str, data: bytes, content_type: str) -> None:
    client.put_object(
        Bucket=bucket,
        Body=data,
        Key=key,
        ContentType=content_type or "application/octet-stream",
    )


def _cos_read(client, bucket: str, key: str) -> tuple[bytes, str]:
    _, _, _, CosServiceError = _cos_sdk()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except CosServiceError as exc:
        status = None
        try:
            status = exc.get_status_code()
            error_code = str(exc.get_error_code() or "")
        except Exception:  # noqa: BLE001
            error_code = ""
        if status == 404 or error_code in ("NoSuchKey", "NoSuchResource"):
            raise _ObjectMissing from exc
        raise

    body = response.get("Body")
    if body is None:
        raise StorageError("对象存储返回内容为空")

    content_type = ""
    # COS 返回头保持线上原始大小写；用大小写不敏感的方式取一次。
    for header_name, header_value in response.items():
        if str(header_name).lower() == "content-type":
            content_type = str(header_value)
            break

    chunks: list[bytes] = []
    try:
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        # 提前失败/读完后都要释放底层连接，否则连接池会慢慢被占满。
        # StreamBody 把 requests.Response 存在 _rt 上，close() 归还连接。
        raw = getattr(body, "_rt", None)
        closer = getattr(raw, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass

    return b"".join(chunks), content_type or guess_content_type(key)


def _cos_head_exists(client, bucket: str, key: str) -> bool:
    _, _, _, CosServiceError = _cos_sdk()
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except CosServiceError as exc:
        try:
            status = exc.get_status_code()
        except Exception:  # noqa: BLE001
            status = 500
        if status == 404:
            return False
        raise


def _cos_delete(client, bucket: str, key: str) -> None:
    _, _, _, CosServiceError = _cos_sdk()
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except CosServiceError as exc:
        try:
            status = exc.get_status_code()
        except Exception:  # noqa: BLE001
            status = 500
        if status == 404:
            return  # 幂等：删不存在的对象不报错
        raise


def _cos_probe(client, bucket: str, key: str) -> None:
    """连通性探针：put -> head -> delete，三步都过才算成功。"""
    client.put_object(Bucket=bucket, Body=b"storage-connectivity-probe", Key=key)
    client.head_object(Bucket=bucket, Key=key)
    client.delete_object(Bucket=bucket, Key=key)


# =============================================================================
# 直传（浏览器 -> COS）：只签 PUT，图片不经服务器中转
# =============================================================================


def _cos_presign_put(
    client, bucket: str, key: str, content_type: str, expires: int
) -> str:
    """生成 PUT 预签名 URL（纯本地 HMAC 计算，不发网络请求）。"""
    return client.get_presigned_url(
        Bucket=bucket,
        Key=key,
        Method="PUT",
        Expired=max(60, int(expires)),
        Headers={"Content-Type": content_type} if content_type else {},
    )


def _cos_head_info(client, bucket: str, key: str) -> tuple[int, str] | None:
    """HEAD 取对象大小与内容类型；对象不存在返回 None。"""
    _, _, _, CosServiceError = _cos_sdk()
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except CosServiceError as exc:
        try:
            status = exc.get_status_code()
        except Exception:  # noqa: BLE001
            status = 500
        if status == 404:
            return None
        raise

    size = 0
    try:
        size = int(
            response.get("Content-Length") or response.get("content-length") or 0
        )
    except (TypeError, ValueError):
        size = 0
    content_type = ""
    for header_name, header_value in response.items():
        if str(header_name).lower() == "content-type":
            content_type = str(header_value)
            break
    return size, content_type or guess_content_type(key)


async def presign_put(
    key: str, content_type: str, db, *, expires: int = 600
) -> str | None:
    """给浏览器直传用的 PUT 预签名 URL。

    COS 模式返回签名 URL；LOCAL 模式（或凭据不全）返回 ``None``，调用方
    必须回退到原有的「multipart 上传到服务器」路径。直传只省带宽：
    文件真实性与大小仍在 commit 阶段核对（HEAD 对象）。
    """
    normalized = normalize_key(key)
    config = await get_storage_config(db)
    if config.mode != _COS_MODE:
        return None
    if not (config.bucket and config.secret_id and config.secret_key):
        return None
    client = _cos_client(config)
    return await anyio.to_thread.run_sync(
        _cos_presign_put, client, config.bucket, normalized, content_type, expires
    )


async def object_info(key: str, db) -> tuple[int, str] | None:
    """返回对象实际大小与内容类型；不存在返回 None。

    commit 阶段用它确认「浏览器确实把文件传到了 COS」，同时兜住预签名 PUT
    无法限制体积的缺口（上传后仍按 10MB 上限校验）。
    """
    normalized = normalize_key(key)
    config = await get_storage_config(db)
    if config.mode != _COS_MODE:
        return None
    if not (config.bucket and config.secret_id and config.secret_key):
        return None
    client = _cos_client(config)
    return await anyio.to_thread.run_sync(
        _cos_head_info, client, config.bucket, normalized
    )


def _cos_read_prefix(client, bucket: str, key: str, length: int) -> bytes:
    """范围读取对象开头若干字节（用于魔数校验）。"""
    _, _, _, CosServiceError = _cos_sdk()
    try:
        response = client.get_object(
            Bucket=bucket, Key=key, Range=f"bytes=0-{max(0, length - 1)}"
        )
    except CosServiceError as exc:
        try:
            status = exc.get_status_code()
        except Exception:  # noqa: BLE001
            status = 500
        if status == 404:
            raise _ObjectMissing from exc
        raise
    body = response.get("Body")
    if body is None:
        return b""
    try:
        return body.read(1024) or b""
    finally:
        raw = getattr(body, "_rt", None)
        closer = getattr(raw, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass


async def read_prefix(key: str, db, *, length: int = 32) -> bytes | None:
    """读取对象开头字节；对象不存在返回 None。

    直传路径没有经过 PIL 解码，commit 时用魔数确认「确实是图片」，
    避免把任意字节挂成 image/png 后由浏览器/第三方渲染。
    """
    normalized = normalize_key(key)
    config = await get_storage_config(db)
    if config.mode != _COS_MODE:
        return None
    if not (config.bucket and config.secret_id and config.secret_key):
        return None
    client = _cos_client(config)
    try:
        return await anyio.to_thread.run_sync(
            _cos_read_prefix, client, config.bucket, normalized, length
        )
    except _ObjectMissing:
        return None


# =============================================================================
# 对外门面
# =============================================================================


async def save_bytes(data: bytes, key: str, content_type: str, db) -> None:
    """把字节流写入当前生效的存储后端（本地磁盘或 COS）。

    调用方必须已经完成业务侧校验（大小/类型/压缩）；这里只管落盘。
    失败统一抛 ``StorageError``（中文信息），由端点层转成 5xx。

    注意：**历史迁移任务不要用这个函数**，它跟随当前 ``storage_mode``。
    迁移必须无条件写 COS，请用 :func:`save_bytes_for_config` 并显式传入
    COS 配置 —— 否则管理员在迁移途中切回 LOCAL，后续文件会被写成"本地
    已存在"，``exists_for_config`` 又跟着查本地，于是被误记为 DONE，COS
    里其实没有对象（2026-11-23 实测踩到）。
    """
    normalized = normalize_key(key)
    config = await get_storage_config(db)
    await save_bytes_for_config(data, normalized, content_type, config)


async def save_bytes_for_config(
    data: bytes,
    key: str,
    content_type: str,
    config: StorageConfig,
    *,
    timeout_seconds: float | None = None,
) -> None:
    """按**显式指定**的配置写入对象（迁移任务/数据库备份专用）。

    与 ``save_bytes`` 的区别只在于「不读当前站点设置」：迁移历史文件时必须
    落到 COS，哪怕此刻 storage_mode 已被切回 LOCAL。

    ``timeout_seconds`` 只对 COS 生效：默认 None 沿用 15s 图片上传短超时；
    数据库备份这类几十 MB 的大文件应显式传 300s，避免慢链路上误判超时。
    """
    normalized = normalize_key(key)
    payload = bytes(data)
    try:
        if config.mode == _COS_MODE:
            # 默认调用保持旧的单参数形式，兼容既有的 client 替身/测试钩子。
            client = (
                _cos_client(config, timeout_seconds=timeout_seconds)
                if timeout_seconds is not None
                else _cos_client(config)
            )
            await anyio.to_thread.run_sync(
                _cos_put, client, config.bucket, normalized, payload, content_type
            )
        else:
            await anyio.to_thread.run_sync(_local_write, normalized, payload)
    except StorageError:
        raise
    except Exception as exc:
        logger.error(
            "storage save failed mode=%s key=%s error=%s",
            config.mode,
            normalized,
            type(exc).__name__,
        )
        raise StorageError("文件保存失败，请稍后重试") from exc


async def read_bytes(key: str, db) -> tuple[bytes, str] | None:
    """读取对象，返回 (bytes, content_type)；当前后端不存在时返回 None。"""
    normalized = normalize_key(key)
    config = await get_storage_config(db)
    return await read_bytes_for_config(normalized, config)


async def read_bytes_for_config(
    key: str, config: StorageConfig, *, timeout_seconds: float | None = None
) -> tuple[bytes, str] | None:
    """按已解析好的配置读取对象。

    单独暴露是为了让 /uploads 回源中间件能「先开一个短会话取配置、关掉会话
    再做网络 I/O」——否则一张几 MB 的图从 COS 拉回来期间会一直占着一条
    数据库连接，图片并发一高就把连接池吃干。

    ``timeout_seconds`` 默认沿用 15s 图片回源短超时；数据库备份下载这类
    几十 MB 的对象应显式传更长超时，避免慢链路误判失败。
    """
    normalized = normalize_key(key)
    try:
        if config.mode == _COS_MODE:
            client = (
                _cos_client(config, timeout_seconds=timeout_seconds)
                if timeout_seconds is not None
                else _cos_client(config)
            )
            return await anyio.to_thread.run_sync(
                _cos_read, client, config.bucket, normalized
            )
        return await anyio.to_thread.run_sync(_local_read, normalized)
    except _ObjectMissing:
        return None
    except StorageError:
        raise
    except Exception as exc:
        logger.error(
            "storage read failed mode=%s key=%s error=%s",
            config.mode,
            normalized,
            type(exc).__name__,
        )
        raise StorageError("文件读取失败，请稍后重试") from exc


async def exists_for_config(key: str, config: StorageConfig) -> bool:
    """按已解析好的配置判断对象是否存在。"""
    normalized = normalize_key(key)
    if config.mode == _COS_MODE:
        client = _cos_client(config)
        return await anyio.to_thread.run_sync(
            _cos_head_exists, client, config.bucket, normalized
        )
    return await anyio.to_thread.run_sync(lambda: _local_path(normalized).is_file())


async def head_size_for_config(key: str, config: StorageConfig) -> int | None:
    """按显式配置读取对象字节数；对象不存在返回 None。

    备份上传后用它核对「写入 COS 的字节数 == 本地 gzip 文件大小」，防止
    SDK 返回成功但对象实际没有落盘（配置错桶等）。COS 侧复用现成的
    ``_cos_head_info``；LOCAL 侧等价于文件大小查询。
    """
    try:
        normalized = normalize_key(key)
    except ValueError as exc:
        raise StorageError("对象路径不合法") from exc

    try:
        if config.mode == _COS_MODE:
            client = _cos_client(config)
            info = await anyio.to_thread.run_sync(
                _cos_head_info, client, config.bucket, normalized
            )
            if info is None:
                return None
            return int(info[0])

        path = _local_path(normalized)

        def _local_size() -> int | None:
            return path.stat().st_size if path.is_file() else None

        return await anyio.to_thread.run_sync(_local_size)
    except StorageError:
        raise
    except Exception as exc:
        logger.error(
            "storage head failed mode=%s key=%s error=%s",
            config.mode,
            normalized,
            type(exc).__name__,
        )
        raise StorageError("文件信息读取失败，请稍后重试") from exc


async def delete_for_config(key: str, config: StorageConfig) -> None:
    """按**显式指定**的配置删除对象（幂等，失败抛中文 ``StorageError``）。

    备份保留清理专用：无论当前站点存储模式是什么，都要从备份实际写入的
    那个 COS 桶里删对象。对象本来就不存在时视为删除成功（COS 404 / 本地
    missing 都不报错）。
    """
    try:
        normalized = normalize_key(key)
    except ValueError as exc:
        raise StorageError("对象路径不合法") from exc

    try:
        if config.mode == _COS_MODE:
            client = _cos_client(config)
            await anyio.to_thread.run_sync(
                _cos_delete, client, config.bucket, normalized
            )
        else:
            await anyio.to_thread.run_sync(
                lambda: _local_path(normalized).unlink(missing_ok=True)
            )
    except StorageError:
        raise
    except Exception as exc:
        logger.error(
            "storage delete failed mode=%s key=%s error=%s",
            config.mode,
            normalized,
            type(exc).__name__,
        )
        raise StorageError("文件删除失败，请稍后重试") from exc


async def read_local_bytes(key: str) -> tuple[bytes, str] | None:
    """只读本地磁盘（COS 模式下作为「尚未迁移的老文件」兜底）。

    供 /uploads 回源中间件在 COS 命中 404 时使用：迁移期间老图还在本地，
    直接回源本地可以避免用户看到 404。
    """
    normalized = normalize_key(key)
    try:
        return await anyio.to_thread.run_sync(_local_read, normalized)
    except Exception:  # noqa: BLE001 - 兜底路径失败等价于文件不存在
        logger.warning("local upload fallback read failed key=%s", normalized, exc_info=True)
        return None


async def exists(key: str, db) -> bool:
    """对象是否存在于当前生效的后端。"""
    normalized = normalize_key(key)
    config = await get_storage_config(db)
    try:
        return await exists_for_config(normalized, config)
    except StorageError:
        raise
    except Exception as exc:
        logger.error(
            "storage exists check failed mode=%s key=%s error=%s",
            config.mode,
            normalized,
            type(exc).__name__,
        )
        raise StorageError("文件检查失败，请稍后重试") from exc


async def delete(key: str, db) -> None:
    """从当前生效的后端删除对象（幂等）。"""
    normalized = normalize_key(key)
    config = await get_storage_config(db)
    try:
        if config.mode == _COS_MODE:
            client = _cos_client(config)
            await anyio.to_thread.run_sync(
                _cos_delete, client, config.bucket, normalized
            )
        else:
            await anyio.to_thread.run_sync(
                lambda: _local_path(normalized).unlink(missing_ok=True)
            )
    except StorageError:
        raise
    except Exception as exc:
        logger.error(
            "storage delete failed mode=%s key=%s error=%s",
            config.mode,
            normalized,
            type(exc).__name__,
        )
        raise StorageError("文件删除失败，请稍后重试") from exc


async def delete_local(key: str) -> None:
    """无条件删除本地磁盘上的对象（清理任务用；COS 上的对象不受影响）。

    清理类任务不应因为单个文件删除失败就整体中断，因此这里只记日志。
    """
    try:
        normalized = (
            key_from_url(key) if key.startswith(_UPLOAD_URL_PREFIX) else normalize_key(key)
        )
        path = _local_path(normalized)
        await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))
    except (ValueError, OSError):
        logger.warning("delete_local failed for %r", key, exc_info=True)


async def test_cos_connection(cfg: StorageConfig) -> tuple[bool, str]:
    """连通性自检：写入探针对象、head 校验、删除探针。

    返回 ``(ok, 中文说明)``；**任何情况下都不抛异常**，方便后台直接展示。
    """
    bucket = _clean(cfg.bucket)
    region = _clean(cfg.region)
    secret_id = _clean(cfg.secret_id)
    secret_key = _clean(cfg.secret_key)
    missing = [
        label
        for label, value in (
            ("SecretId", secret_id),
            ("SecretKey", secret_key),
            ("Bucket", bucket),
            ("Region", region),
        )
        if not value
    ]
    if missing:
        return False, f"COS 配置不完整：缺少 {'、'.join(missing)}"

    probe_key = f"probe/connectivity-{uuid4().hex}.txt"
    try:
        probe_cfg = StorageConfig(
            mode=_COS_MODE,
            secret_id=secret_id,
            secret_key=secret_key,
            bucket=bucket,
            region=region,
            cdn_domain=None,
        )
        client = _cos_client(probe_cfg)
        await anyio.to_thread.run_sync(_cos_probe, client, bucket, probe_key)
    except Exception as exc:  # noqa: BLE001 - 自检永不上抛
        detail = _describe_cos_error(exc)
        logger.warning(
            "COS connectivity test failed bucket=%s region=%s reason=%s",
            bucket,
            region,
            detail,
        )
        return False, f"COS 连接失败：{detail}"
    return True, "COS 连接成功，读写与删除均正常"
