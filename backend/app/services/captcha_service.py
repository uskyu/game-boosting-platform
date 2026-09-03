"""图形验证码服务：进程内存存储，一次性消费，TTL 5 分钟."""

import secrets
import time
import uuid

from captcha.image import ImageCaptcha

# 去掉易混淆字符 0o1il
_CHARSET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
_TTL_SECONDS = 5 * 60

# captcha_id -> (code, expire_at)
_store: dict[str, tuple[str, float]] = {}

# IP 限流：ip -> [窗口起始时间, 计数]
_rate: dict[str, list[float]] = {}
_RATE_WINDOW = 60
_RATE_LIMIT = 20

_image_captcha = ImageCaptcha(width=160, height=60)


def _purge_expired(now: float | None = None) -> None:
    now = time.time() if now is None else now
    expired = [k for k, (_, exp) in _store.items() if exp <= now]
    for k in expired:
        del _store[k]


def create() -> tuple[str, bytes]:
    """生成验证码，返回 (captcha_id, png_bytes)."""
    code = "".join(secrets.choice(_CHARSET) for _ in range(4))
    captcha_id = uuid.uuid4().hex
    _store[captcha_id] = (code, time.time() + _TTL_SECONDS)
    png_bytes = _image_captcha.generate(code).getvalue()
    return captcha_id, png_bytes


def verify(captcha_id: str, code: str) -> bool:
    """校验验证码，大小写不敏感，一次性消费。过期/不存在/已用返回 False."""
    if not captcha_id or not code:
        return False
    entry = _store.pop(captcha_id, None)
    if entry is None:
        return False
    expected, expire_at = entry
    if time.time() > expire_at:
        return False
    return expected.lower() == code.strip().lower()


def check_rate_limit(ip: str) -> bool:
    """同一 IP 1 分钟最多 20 次，返回 True 表示允许，False 表示超限."""
    now = time.time()
    window_start, count = _rate.get(ip, (0.0, 0))
    if now - window_start >= _RATE_WINDOW:
        _rate[ip] = [now, 1]
        return True
    if count >= _RATE_LIMIT:
        return False
    _rate[ip][1] += 1
    return True
