"""图形验证码服务：进程内存存储，一次性消费，TTL 5 分钟。

2026-10-22 起支持场景（``register`` = 注册，``accept`` = 接单）：
- 注册码不能拿去接单、接单码不能拿去注册；
- 限流按「场景 + 来源 IP」分桶，接单另有按用户的分桶；
- 生成时顺带清理过期验证码与过期限流窗口（原实现的 ``_purge_expired``
  从未被调用，过期记录会一直占内存）。

2026-10-23 修复「抢单高峰验证码加载不出来」：限流用的 IP 必须来自
``app.core.client_ip``（回溯 X-Forwarded-For），不能直接用
``request.client.host``——后者在后端容器里恒为前端容器的 docker 内网 IP，
会把全站用户算成同一个桶。同时接单场景限流放宽到 60 次/分钟（抢单场景
用户会反复开弹窗/点图刷新）。

注意：存储是**单进程内存**。当前部署是单 uvicorn 进程，没问题；将来加
worker / 多副本时必须换成 Redis，否则会出现"随机提示验证码错误"。
"""

import secrets
import time
import uuid

from captcha.image import ImageCaptcha

# 去掉易混淆字符 0o1il
_CHARSET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
_TTL_SECONDS = 5 * 60
_SCENES = ("register", "accept")

# captcha_id -> (code, expire_at)
_store: dict[str, tuple[str, float]] = {}
# captcha_id -> scene（与 _store 同步增删；单独存是为了兼容既有测试按
# 二元组解包 _store 的写法）
_scenes: dict[str, str] = {}

# 限流键 -> [窗口起始时间, 计数]
_rate: dict[str, list[float]] = {}
_RATE_WINDOW = 60
_RATE_LIMIT = 20

# 场景默认限流值：接单是「抢单」场景，用户会反复开弹窗/点图刷新，20 次/分钟
# 远不够（2026-10-23 晚高峰 31 人同时抢单即打满），因此单独放宽；注册保持严格。
# 可用 settings.CAPTCHA_RATE_LIMIT_* 覆盖。
_SCENE_RATE_LIMITS = {"register": 20, "accept": 60}

_image_captcha = ImageCaptcha(width=160, height=60)


def _normalize_scene(scene: str | None) -> str:
    value = (scene or "register").strip().lower()
    return value if value in _SCENES else "register"


def rate_limit_for(scene: str | None) -> int:
    """返回该场景的每分钟限流次数（配置优先，缺省用场景默认值）。"""
    normalized = _normalize_scene(scene)
    from app.core.config import settings

    if normalized == "accept":
        return int(
            getattr(settings, "CAPTCHA_RATE_LIMIT_ACCEPT", None)
            or _SCENE_RATE_LIMITS["accept"]
        )
    return int(
        getattr(settings, "CAPTCHA_RATE_LIMIT_REGISTER", None)
        or _SCENE_RATE_LIMITS["register"]
    )


def _purge_expired(now: float | None = None) -> None:
    now = time.time() if now is None else now
    expired = [k for k, (_, exp) in _store.items() if exp <= now]
    for key in expired:
        _store.pop(key, None)
        _scenes.pop(key, None)
    stale = [
        key
        for key, (window_start, _count) in _rate.items()
        if now - window_start >= _RATE_WINDOW
    ]
    for key in stale:
        del _rate[key]


def create(scene: str = "register") -> tuple[str, bytes]:
    """生成验证码，返回 (captcha_id, png_bytes)."""
    scene = _normalize_scene(scene)
    _purge_expired()
    code = "".join(secrets.choice(_CHARSET) for _ in range(4))
    captcha_id = uuid.uuid4().hex
    _store[captcha_id] = (code, time.time() + _TTL_SECONDS)
    _scenes[captcha_id] = scene
    png_bytes = _image_captcha.generate(code).getvalue()
    return captcha_id, png_bytes


def verify(captcha_id: str | None, code: str | None, scene: str = "register") -> bool:
    """校验验证码，大小写不敏感，一次性消费。过期/不存在/已用/场景不符返回 False."""
    scene = _normalize_scene(scene)
    if not captcha_id or not code:
        return False
    entry = _store.pop(captcha_id, None)
    entry_scene = _scenes.pop(captcha_id, None)
    if entry is None:
        return False
    expected, expire_at = entry
    if time.time() > expire_at:
        return False
    if entry_scene != scene:
        return False
    return expected.lower() == code.strip().lower()


def check_rate_limit(key: str, limit: int = _RATE_LIMIT) -> bool:
    """滑动窗口限流：同一 key 每 60 秒最多 limit 次。

    Returns:
        True 表示允许，False 表示超限。
    """
    now = time.time()
    window_start, count = _rate.get(key, (0.0, 0))
    if now - window_start >= _RATE_WINDOW:
        _rate[key] = [now, 1]
        return True
    if count >= limit:
        return False
    _rate[key][1] += 1
    return True


def scene_rate_key(scene: str, ip: str | None) -> str:
    return f"{_normalize_scene(scene)}:ip:{ip or 'unknown'}"


def user_rate_key(scene: str, user_id: int) -> str:
    return f"{_normalize_scene(scene)}:user:{user_id}"
