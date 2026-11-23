"""COS 私有读图片的预签名 URL 服务（签名 + 权限 + 复用缓存）。

背景：切到腾讯云 COS 后存储桶是**私有读写**，浏览器不能直接 GET 对象，必须
由服务端签发带签名的 URL。为了把服务器带宽降到零，图片一律由浏览器直连 COS，
服务端只在「序列化响应时」把 ``/uploads/<key>`` 改写成签名 URL。

三个必须同时满足的约束：

1. **稳定**：同一个对象 key 在同一 7 天窗口内必须拿到同一串 URL，否则浏览器
   的 HTTP 缓存键每次都变，等于每次打开页面都重新下载。
   COS SDK 的 ``get_presigned_url`` 每次都写新的 ``q-sign-time``，字符串必然
   不同；因此本模块把首次签发的 URL 落库（``signed_url_cache``），窗口内原样
   复用。到期前不足 1 小时才重新签发并覆盖同一行（见 ``SIGN_SAFETY_...``）。
2. **不泄露**：签名 URL 只含 AKID（SecretId）与签名参数，绝不含 SecretKey；
   本模块的日志也只输出 key 与异常类型，绝不打印配置或 URL 参数。
3. **权限在签发时判定**：COS 对象本身无鉴权（拿到 URL 的人在有效期内都能读），
   所以「谁能看这张图」必须在签发这一刻算清楚。替用户签发一个无权访问的
   key 就等于把该对象公开 7 天。

存储模式仍由 ``storage_service.get_storage_config`` 决定：LOCAL 模式本模块
完全不介入（返回 None / 原字符串），历史 ``/uploads`` 静态直读行为不变。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import anyio
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.models.chat import ConversationParticipant, Message
from app.models.order import Order, OrderClaim
from app.models.order_after_sale import OrderAfterSale
from app.models.signed_url_cache import SignedUrlCache
from app.models.user import User, UserRole
from app.models.withdrawal import WithdrawalRequest

logger = logging.getLogger(__name__)

# 签名窗口：7 天。与老板确认的「7 天有效」一致，也是 COS 预签名允许的常规上限。
SIGN_WINDOW_SECONDS = 7 * 24 * 3600
# 剩余有效期不足 1 小时就提前重新签发：避免用户在「还剩 3 分钟」时打开页面，
# 然后一边看一边过期（浏览器缓存里那张图会突然 403）。
SIGN_SAFETY_REMAINING_SECONDS = 3600

_UPLOADS_PREFIX = "/uploads/"
_COS_MODE = "COS"
# 拒绝签发时对外的统一文案：不区分「无权访问」与「对象不存在」。
_FORBIDDEN_MESSAGE = "无权访问该图片"

# COS SDK 是同步阻塞的（requests），所有调用必须走线程池。
_COS_TIMEOUT_HINT = "图片签名服务暂不可用，请稍后重试"

# (bucket, region, secret_id) -> CosS3Client。签名不访问网络，复用 client 只是
# 为了避免每次签名都重建配置对象；缓存与 storage_service 的 client 缓存互不影响。
_CLIENT_CACHE: dict[tuple[str, str, str], Any] = {}

# 过期行清理节流：这是纯运维动作，不能挂在每个大厅轮询请求上（大厅每 10 秒
# 就来一次，每次多一条写语句是纯粹的写放大）。进程内每小时最多清一次。
_CLEANUP_INTERVAL_SECONDS = 3600.0
_last_cleanup_at: float = 0.0


# =============================================================================
# 窗口与 key 工具
# =============================================================================


def next_expire_at(now: int | None = None) -> int:
    """返回当前 7 天窗口的结束瞬间（Unix 秒，UTC）。

    用「窗口对齐」而不是「now + 7 天」：只有这样，同一窗口内的所有请求才会
    得到同一个 ``expire_at``，签名台账的 ``(object_key, expire_at)`` 唯一键
    才能真正命中复用。实现即 ``(floor(now / window) + 1) * window``。
    """
    current = int(time.time()) if now is None else int(now)
    window = SIGN_WINDOW_SECONDS
    return (current // window + 1) * window


def _normalize_key(key: str) -> str | None:
    """对象 key 规范化：相对 POSIX 路径、无前导斜杠、拒绝路径穿越。

    合法样例：``orders/abc.jpg``、``chat/def.png``、``withdrawal-qr/7/x.png``。
    与 ``storage_service.normalize_key`` 同一口径（本模块自带一份是为了在
    storage_service 尚未就绪时也能安全拒绝非法输入）。
    """
    if not isinstance(key, str):
        return None
    value = key.strip()
    if not value or "\x00" in value:
        return None
    if value.startswith("/") or "\\" in value:
        return None
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    if ":" in parts[0]:
        return None
    return "/".join(parts)


def key_from_url(url: str) -> str | None:
    """``/uploads/orders/x.jpg`` → ``orders/x.jpg``；非法/非本站路径返回 None。

    只接受以 ``/uploads/`` 开头的站内相对路径：绝对 URL（任何 scheme）、
    查询串、片段、反斜杠与 ``..`` 一律拒绝，避免调用方拿任意字符串去探测
    COS 对象或拼出越权 key。
    """
    if not isinstance(url, str):
        return None
    value = url.strip()
    if not value or value == "/uploads" or value == _UPLOADS_PREFIX:
        return None
    if not value.startswith(_UPLOADS_PREFIX):
        return None
    # 绝对 URL、协议相对 URL、查询串/片段、编码穿越都不属于「库里存的相对路径」。
    if "?" in value or "#" in value or "%" in value or "\\" in value:
        return None
    if "://" in value:
        return None
    return _normalize_key(value[len(_UPLOADS_PREFIX):])


def is_managed_url(url: str) -> bool:
    """是否为本模块管理的站内上传路径（``/uploads/...`` 且 key 合法）。"""
    return isinstance(url, str) and key_from_url(url) is not None


# =============================================================================
# 配置 / client
# =============================================================================


async def _load_config(db) -> Any:
    """读取当前生效的存储配置；任何异常都按「非 COS」处理（回落到原 URL）。

    显式返回 None 表示「不要签名」：签名是体验优化，配置读取失败时宁可让
    浏览器走原有 /uploads 回源，也不能让整个订单接口 500。
    """
    try:
        # 延迟 import：storage_service 属于同一功能的另一个模块，缺失时本模块
        # 仍可安全导入（所有函数退化为「不签名」）。
        from app.services import storage_service

        config = await storage_service.get_storage_config(db)
    except Exception:  # noqa: BLE001 - 配置读取失败不得影响业务响应
        logger.warning("signed url config lookup failed; falling back to raw urls", exc_info=True)
        return None
    return config


def _build_client(config: Any) -> Any:
    """按 (bucket, region, secret_id) 懒建 COS client。

    ``get_presigned_url`` 是纯本地 HMAC 计算，不发网络请求；client 缓存只是
    省掉重复构造配置对象的开销。
    """
    cache_key = (
        str(getattr(config, "bucket", "") or ""),
        str(getattr(config, "region", "") or ""),
        str(getattr(config, "secret_id", "") or ""),
    )
    client = _CLIENT_CACHE.get(cache_key)
    if client is not None:
        return client

    from qcloud_cos import CosConfig, CosS3Client

    client = CosS3Client(
        CosConfig(
            Region=config.region,
            SecretId=config.secret_id,
            SecretKey=config.secret_key,
            Scheme="https",
        )
    )
    _CLIENT_CACHE[cache_key] = client
    return client


def _presign(client: Any, bucket: str, key: str, expired: int) -> str:
    """同步调用 SDK 签发 7 天 URL（由 ``anyio.to_thread`` 调用）。"""
    return client.get_presigned_url(
        Bucket=bucket,
        Key=key,
        Method="GET",
        Expired=expired,
    )


def _expire_dt(expire_at: int) -> datetime:
    """Unix 秒 → 库内统一的 naive UTC DateTime。"""
    return datetime.fromtimestamp(expire_at, tz=timezone.utc).replace(tzinfo=None)


def _now_dt(now: int) -> datetime:
    return datetime.fromtimestamp(now, tz=timezone.utc).replace(tzinfo=None)


# =============================================================================
# 「对象真的在 COS 里吗」校验
# =============================================================================
#
# 为什么必须有这一层（2026-11-23 线上事故）：
#   历史图片全部落在本地磁盘，数据库里也是 ``/uploads/<key>``。切到 COS 后
#   如果无脑把每个 /uploads 都签成 COS 直链，浏览器就会绕过 /uploads 回源
#   中间件直接打 COS —— 而这些对象根本还没迁移，结果**所有历史图片都打不开**
#   （实测生产订单附件在 COS 里是 MISSING，接口却返回了签名 URL）。
#
# 正确语义是：**只有确认存在于 COS 的对象才直连 COS，否则保留 /uploads 相对
# 路径**，让中间件自己按 COS → 本地磁盘的顺序回源。这样部分迁移期间两种图
# 都能打开，也符合「本地历史图保持匿名可读」的产品决定。
#
# 代价：每次签发多一次 HEAD 往返。因此结果进进程内缓存（含「不存在」的否定
# 结果），TTL 远短于签名窗口：迁移完成后最多等待一个 TTL 就会自动变为直连。

# key -> (是否存在, 缓存到期时刻 monotonic)
_EXISTS_CACHE: dict[str, tuple[bool, float]] = {}
_EXISTS_CACHE_TTL_SECONDS = 120.0
# 单次 HEAD 的并发上限：一次请求可能带几十张图，全打并发会把 COS 打爆。
_EXISTS_CONCURRENCY = 8


def _prune_exists_cache(now_monotonic: float) -> None:
    """清掉过期的存在性缓存条目（低频、便宜，避免无界增长）。"""
    stale = [k for k, (_, exp) in _EXISTS_CACHE.items() if exp <= now_monotonic]
    for key in stale:
        _EXISTS_CACHE.pop(key, None)


async def _filter_existing_in_cos(config: Any, keys: list[str]) -> list[str]:
    """返回确实存在于 COS 的 key 子集；HEAD 失败/网络异常一律按「不存在」处理。

    失败时保守地不签名，让浏览器走 ``/uploads`` 回源中间件：图片最多慢一点，
    绝不会因为 COS 抖动而整批开不出图。
    """
    if not keys:
        return []

    from app.services import storage_service

    now_monotonic = time.monotonic()
    pending: list[str] = []
    confirmed: list[str] = []
    for key in keys:
        entry = _EXISTS_CACHE.get(key)
        if entry is not None and entry[1] > now_monotonic:
            if entry[0]:
                confirmed.append(key)
            continue
        pending.append(key)

    if pending:
        _prune_exists_cache(now_monotonic)
        semaphore = asyncio.Semaphore(_EXISTS_CONCURRENCY)

        async def probe(key: str) -> tuple[str, bool]:
            async with semaphore:
                try:
                    exists = await storage_service.exists_for_config(key, config)
                except Exception:  # noqa: BLE001 - HEAD 失败按「不在 COS」处理
                    logger.warning("cos head failed key=%s; treat as missing", key)
                    exists = False
            return key, bool(exists)

        results = await asyncio.gather(*(probe(key) for key in pending))
        expires = time.monotonic() + _EXISTS_CACHE_TTL_SECONDS
        for key, exists in results:
            _EXISTS_CACHE[key] = (exists, expires)
            if exists:
                confirmed.append(key)

    return confirmed


def invalidate_exists_cache() -> None:
    """清空存在性缓存（迁移/清理任务结束时调用，让新状态立刻生效）。"""
    _EXISTS_CACHE.clear()


# =============================================================================
# 批量签发（带台账复用）
# =============================================================================


async def _sign_keys(db, keys: list[str]) -> dict[str, str]:
    """把一批对象 key 签成 URL，返回 ``key -> url``；非 COS 模式返回空字典。

    一次调用只做固定次数的 DB 往返（1 次查缓存行 + 1 次清理过期行 + 1 次
    批量 upsert + 1 次回读），与 key 数量无关；签名本身是本地 HMAC 计算。
    """
    unique_keys = sorted({key for key in keys if key})
    if not unique_keys:
        return {}

    config = await _load_config(db)
    if config is None or getattr(config, "mode", None) != _COS_MODE:
        return {}
    if not all(
        getattr(config, field, None)
        for field in ("bucket", "region", "secret_id", "secret_key")
    ):
        # 后台选了 COS 但凭据不全（storage_service 通常会回退 LOCAL，这里兜底）
        return {}

    # 关键一步：只签「确实存在于 COS」的对象。未迁移的历史图留在 /uploads，
    # 由回源中间件按 COS → 本地顺序提供服务（否则浏览器直连 COS 会 404）。
    unique_keys = await _filter_existing_in_cos(config, unique_keys)
    if not unique_keys:
        return {}

    now = int(time.time())
    expire_at_int = next_expire_at(now)
    remaining = expire_at_int - now
    expire_at_dt = _expire_dt(expire_at_int)

    result = await db.execute(
        select(SignedUrlCache.object_key, SignedUrlCache.url).where(
            SignedUrlCache.object_key.in_(unique_keys),
            SignedUrlCache.expire_at == expire_at_dt,
        )
    )
    cached = {row.object_key: row.url for row in result}

    await _cleanup_stale_rows(db, now)

    # 剩余有效期不足 1 小时时即使命中缓存也重新签发（同一个 expire_at 行被覆盖）。
    need_sign = [
        key for key in unique_keys
        if key not in cached or remaining <= SIGN_SAFETY_REMAINING_SECONDS
    ]
    if not need_sign:
        return cached

    try:
        client = _build_client(config)
        bucket = config.bucket
        signed = await anyio.to_thread.run_sync(
            lambda: {
                key: _presign(client, bucket, key, max(1, remaining))
                for key in need_sign
            }
        )
    except Exception:  # noqa: BLE001 - 签名失败只降级为原 URL，不影响响应
        logger.warning("cos presign failed keys=%s", need_sign, exc_info=True)
        return cached

    rows = [
        {"object_key": key, "expire_at": expire_at_dt, "url": url}
        for key, url in signed.items()
        if isinstance(url, str) and url
    ]
    if rows:
        statement = mysql_insert(SignedUrlCache).values(rows)
        statement = statement.on_duplicate_key_update(url=statement.inserted.url)
        await db.execute(statement)

    # 回读而不是直接用本地生成的串：并发请求可能同时签发同一个 key，回读保证
    # 同一窗口内对外始终是「台账里那一串」，浏览器缓存键不会因并发而抖动。
    result = await db.execute(
        select(SignedUrlCache.object_key, SignedUrlCache.url).where(
            SignedUrlCache.object_key.in_(need_sign),
            SignedUrlCache.expire_at == expire_at_dt,
        )
    )
    return {row.object_key: row.url for row in result}


async def _cleanup_stale_rows(db, now: int) -> None:
    """低频清理台账里已经彻底过期（到期超过 1 小时）的行。

    COS 签名的 ``expire_at`` 是整行唯一键的一部分，窗口翻页后每个 key 都会
    多一行；不清就会随窗口数线性增长。清理按 ``expire_at`` 索引扫，且进程内
    每小时最多执行一次——大厅每 10 秒轮询一次，清理绝不能挂在热路径上。
    """
    global _last_cleanup_at
    monotonic_now = time.monotonic()
    if monotonic_now - _last_cleanup_at < _CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup_at = monotonic_now
    # 已过期超过安全余量的行：即使被复用也只剩不足 1 小时，前端本来就会重新签发。
    cutoff = _now_dt(now - SIGN_SAFETY_REMAINING_SECONDS)
    try:
        await db.execute(delete(SignedUrlCache).where(SignedUrlCache.expire_at < cutoff))
    except Exception:  # noqa: BLE001 - 清理失败不影响本次签名
        logger.warning("signed url cache cleanup failed", exc_info=True)


async def sign_url(db, key: str) -> str | None:
    """为单个对象 key 签发（或复用）URL。

    Returns:
        签名 URL；非 COS 模式、key 非法或签名失败时返回 None（调用方据此
        回落到原来的 ``/uploads/...``，绝不因此报错）。
    """
    normalized = _normalize_key(key)
    if normalized is None:
        return None
    signed = await _sign_keys(db, [normalized])
    return signed.get(normalized)


# =============================================================================
# 响应体改写
# =============================================================================


def _collect_managed_urls(value: Any, sink: list[str]) -> None:
    """递归收集 dict/list/str/BaseModel 里的 ``/uploads/...`` 字符串。"""
    if isinstance(value, str):
        if is_managed_url(value):
            sink.append(value)
        return
    if isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            _collect_managed_urls(getattr(value, field_name, None), sink)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_managed_urls(item, sink)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_managed_urls(item, sink)


def _rewrite_managed_urls(value: Any, signed: dict[str, str]) -> Any:
    """把结构里的 ``/uploads`` 串替换成已签发的 URL；未命中签名的保持原样。

    对 pydantic 模型用 ``model_copy(update=...)`` 生成新实例（不改原对象），
    因此订单详情/列表的 raw 数据不会被污染；对 dict/list 同样返回新结构，
    调用方必须使用返回值。
    """
    if isinstance(value, str):
        if not is_managed_url(value):
            return value
        key = key_from_url(value)
        replacement = signed.get(key) if key else None
        return replacement or value
    if isinstance(value, BaseModel):
        updates: dict[str, Any] = {}
        for field_name in type(value).model_fields:
            current = getattr(value, field_name, None)
            rewritten = _rewrite_managed_urls(current, signed)
            if rewritten is not current:
                updates[field_name] = rewritten
        if not updates:
            return value
        return value.model_copy(update=updates)
    if isinstance(value, dict):
        changed = False
        result: dict[Any, Any] = {}
        for item_key, item in value.items():
            rewritten = _rewrite_managed_urls(item, signed)
            if rewritten is not item:
                changed = True
            result[item_key] = rewritten
        return result if changed else value
    if isinstance(value, tuple):
        rewritten_items = tuple(_rewrite_managed_urls(item, signed) for item in value)
        return rewritten_items if any(a is not b for a, b in zip(value, rewritten_items)) else value
    if isinstance(value, list):
        changed = False
        rewritten_items = []
        for item in value:
            rewritten = _rewrite_managed_urls(item, signed)
            if rewritten is not item:
                changed = True
            rewritten_items.append(rewritten)
        return rewritten_items if changed else value
    return value


async def sign_urls_in_payload(db, obj):
    """把响应体里所有 COS 托管的 ``/uploads`` 路径改写成签名 URL。

    支持 dict / list / str / pydantic 模型（含嵌套）；LOCAL 模式、无 URL、
    签名失败时原样返回（**始终返回可用对象**，调用方必须使用返回值）。
    """
    if obj is None:
        return obj

    config = await _load_config(db)
    if config is None or getattr(config, "mode", None) != _COS_MODE:
        return obj

    urls: list[str] = []
    _collect_managed_urls(obj, urls)
    if not urls:
        return obj

    keys = [key for key in (key_from_url(url) for url in urls) if key]
    signed = await _sign_keys(db, keys)
    if not signed:
        return obj
    return _rewrite_managed_urls(obj, signed)


# =============================================================================
# 权限判定
# =============================================================================


def _is_admin(user: User | None) -> bool:
    return user is not None and getattr(user, "role", None) == UserRole.ADMIN


def _json_contains_candidates(url: str) -> list[Any]:
    """JSON 列包含判断的候选值：兼容 ``[{"url": ...}]`` 与 ``["/uploads/..."]``。"""
    return [
        json.dumps({"url": url}),
        json.dumps(url),
    ]


async def _order_ids_with_url(db, column, url: str) -> set[int]:
    """找出 JSON 列里引用了该 URL 的订单 id（对象数组与纯字符串数组都覆盖）。"""
    conditions = [
        func.json_contains(column, candidate)
        for candidate in _json_contains_candidates(url)
    ]
    result = await db.execute(select(Order.id).where(or_(*conditions)))
    return {int(row[0]) for row in result}


async def _authorize_order_attachment(db, user: User, url: str) -> bool:
    """订单附件/交付附件：发布人、该单任意报名打手（OrderClaim）、管理员。"""
    order_ids = await _order_ids_with_url(db, Order.attachments, url)
    order_ids |= await _order_ids_with_url(db, Order.delivery_attachments, url)
    if not order_ids:
        claim_result = await db.execute(
            select(OrderClaim.order_id).where(
                or_(
                    *(
                        func.json_contains(OrderClaim.delivery_attachments, candidate)
                        for candidate in _json_contains_candidates(url)
                    )
                )
            )
        )
        order_ids = {int(row[0]) for row in claim_result}
    if not order_ids:
        return False

    result = await db.execute(
        select(Order.id, Order.user_id).where(Order.id.in_(order_ids))
    )
    owners = {int(row.id): int(row.user_id) for row in result}
    if any(owner_id == user.id for owner_id in owners.values()):
        return True

    claim_result = await db.execute(
        select(OrderClaim.id).where(
            OrderClaim.order_id.in_(order_ids),
            OrderClaim.booster_id == user.id,
        )
    )
    return claim_result.first() is not None


async def _authorize_chat_image(db, user: User, url: str) -> bool:
    """聊天图片：该会话参与者（含被拉进会话的管理员）。"""
    result = await db.execute(
        select(Message.conversation_id).where(Message.content == url)
    )
    conversation_ids = {int(row[0]) for row in result}
    if not conversation_ids:
        return False
    participant = await db.execute(
        select(ConversationParticipant.id).where(
            ConversationParticipant.conversation_id.in_(conversation_ids),
            ConversationParticipant.user_id == user.id,
        )
    )
    return participant.first() is not None


def _owner_from_path(url: str) -> tuple[str | None, int | None]:
    """按路径约定解析归属用户：提现二维码 / 售后凭证 / 代练申请证明。

    这些目录由上传端点按「当前用户 id」建立，文件名本身无关紧要；不属于
    这三种约定的路径返回 ``(None, None)``。
    """
    key = key_from_url(url)
    if key is None:
        return None, None
    parts = key.split("/")
    # /uploads/withdrawals/{user_id}/xxx  -> withdrawals/uuid 目录
    if len(parts) >= 3 and parts[0] == "withdrawals" and parts[1].isdigit():
        return "withdrawal", int(parts[1])
    # /uploads/aftersales/{user_id}/xxx
    if len(parts) >= 3 and parts[0] == "aftersales" and parts[1].isdigit():
        return "after_sale", int(parts[1])
    # /uploads/booster-proof-{user_id}-{uuid}.ext（users.py 的历史命名）
    if len(parts) == 1 and parts[0].startswith("booster-proof-"):
        rest = parts[0][len("booster-proof-"):]
        user_part, _, _tail = rest.partition("-")
        if user_part.isdigit():
            return "booster_application", int(user_part)
    return None, None


async def authorize_url(db, user: User | None, url: str) -> bool:
    """判断 ``user`` 是否有权读取这个 ``/uploads`` URL 旁边的 COS 对象。

    管理员始终放行；未登录一律拒绝。命中不了的路径一律拒绝（宁可少签一次，
    也不能把「存在但不在任何业务表里」的对象公开）。

    注意本函数只回答「能不能读」，不判断对象是否真实存在：对外文案统一为
    无权访问，绝不能成为「对象是否存在」的探测接口。
    """
    if user is None:
        return False
    if _is_admin(user):
        return True
    if not is_managed_url(url):
        return False

    key = key_from_url(url)
    if key is None:
        return False

    kind, owner_id = _owner_from_path(url)
    if owner_id is not None:
        if kind == "withdrawal":
            # 提现二维码：仅申请人本人（管理员已在上方放行）
            if owner_id == user.id:
                return True
            # 归属目录之外的提现单若引用了该图（理论上不可能），仍按归属判定
            return False
        if owner_id == user.id:
            return True
        return False

    if key.startswith("chat/"):
        return await _authorize_chat_image(db, user, url)
    if key.startswith("orders/") or key.startswith("deliveries/"):
        return await _authorize_order_attachment(db, user, url)
    return False


async def resign_for_user(db, user: User | None, raw_url: str) -> str | None:
    """权限校验通过后重新签发一个 URL（供 ``POST /storage/resign``）。

    Returns:
        新的签名 URL / 原 URL（LOCAL 模式）；无权访问或输入非法时返回 None，
        由端点统一转成 403（文案不区分「不存在」与「无权限」）。
    """
    if not is_managed_url(raw_url):
        return None
    if not await authorize_url(db, user, raw_url):
        return None

    config = await _load_config(db)
    if config is None or getattr(config, "mode", None) != _COS_MODE:
        # LOCAL 模式本来就是匿名的历史行为：原样返回，前端无需再处理。
        return raw_url

    key = key_from_url(raw_url)
    if key is None:
        return None
    return await sign_url(db, key)
