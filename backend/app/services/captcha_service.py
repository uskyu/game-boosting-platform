"""图形验证码服务：进程内存存储，一次性消费，TTL 5 分钟。

2026-10-22 起支持场景（``register`` = 注册，``accept`` = 接单）：
- 注册码不能拿去接单、接单码不能拿去注册；
- 生成时顺带清理过期验证码与过期限流窗口（原实现的 ``_purge_expired``
  从未被调用，过期记录会一直占内存）。

限流分场景设计（2026-10-23 事故后确定）
--------------------------------------
- **注册**：没有账号，只能按「来源 IP」分桶，20 次/分钟。IP 必须用
  ``app.core.client_ip`` 解析出的真实地址（不能直接用 ``request.client.host``，
  它在容器里恒为前端容器 IP）。
- **接单**：接口强制登录，**只按用户**分桶，60 次/分钟。不叠加 IP 桶——
  同网吧/同 NAT 的正常打手会互相挤占，而「一人多号刷图」的收益极低，不值得
  为此牺牲抢单体验。

2026-10-23 事故：接单曾同时按 IP 限流，且 IP 取错（全站共用容器 IP 一个桶，
20 次/分钟），晚高峰 31 人抢单时集体 429，前端表现为「验证码加载不出来」。

2026-11-11 反自动化加固：接单码额外绑定 ``order_id + user_id``，只能用于
指定订单、只能由指定账号提交；跨单/跨账号复用一律校验失败。接单接口还会
额外施加 1 次/秒突发 + 20 次/分钟的通用限流（见 ``app/api/rate_limit.py``）。

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
# 接单验证码绑定：captcha_id -> 订单 ID / 用户 ID。注册码不写这两张表；
# 接单码必须写，verify 时携带同样的 subject/user_id 才能通过，杜绝跨单、
# 跨账号复用（2026-11-11 反抢单器加固）。
_subjects: dict[str, str] = {}
_users: dict[str, int] = {}
# (订单 ID, 用户 ID) -> 当前有效的 captcha_id。同一用户同一订单只保留最新
# 一张码，重新获取会立即作废上一张：脚本无法预取多张码在放行时轮换重试。
_bindings: dict[tuple[str, int], str] = {}

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
        _subjects.pop(key, None)
        _users.pop(key, None)
    # 绑定索引只指向仍存在的验证码；过期条目随 _store 一起清掉。
    for binding, captcha_id in list(_bindings.items()):
        if captcha_id not in _store:
            _bindings.pop(binding, None)
    stale = [
        key
        for key, (window_start, _count) in _rate.items()
        if now - window_start >= _RATE_WINDOW
    ]
    for key in stale:
        del _rate[key]


def create(
    scene: str = "register",
    *,
    subject: str | int | None = None,
    user_id: int | None = None,
) -> tuple[str, bytes]:
    """生成验证码，返回 ``(captcha_id, png_bytes)``。

    Args:
        scene: ``register`` 或 ``accept``。
        subject: 接单场景绑定到的订单 ID（字符串化后写入）。注册场景不传。
        user_id: 接单场景绑定到的用户 ID。注册场景不传。

    绑定信息与验证码同生命周期，``verify`` 必须携带一致的值。没有绑定信息
    的验证码只能用于不带宽带的场景（注册），不能拿去接单。接单场景下同一
    用户同一订单重新取码会立即作废上一张，脚本无法靠预取多张码轮换重试。
    """
    scene = _normalize_scene(scene)
    _purge_expired()
    code = "".join(secrets.choice(_CHARSET) for _ in range(4))
    captcha_id = uuid.uuid4().hex
    binding: tuple[str, int] | None = None
    if scene == "accept" and subject is not None and user_id is not None:
        binding = (str(subject), int(user_id))
        previous = _bindings.pop(binding, None)
        if previous is not None:
            _store.pop(previous, None)
            _scenes.pop(previous, None)
            _subjects.pop(previous, None)
            _users.pop(previous, None)
    _store[captcha_id] = (code, time.time() + _TTL_SECONDS)
    _scenes[captcha_id] = scene
    if subject is not None:
        _subjects[captcha_id] = str(subject)
    if user_id is not None:
        _users[captcha_id] = int(user_id)
    if binding is not None:
        _bindings[binding] = captcha_id
    png_bytes = _image_captcha.generate(code).getvalue()
    return captcha_id, png_bytes


def verify(
    captcha_id: str | None,
    code: str | None,
    scene: str = "register",
    *,
    subject: str | int | None = None,
    user_id: int | None = None,
) -> bool:
    """校验验证码，大小写不敏感，一次性消费。

    过期/不存在/已用/场景不符/绑定信息不一致一律返回 False。绑定规则：

    - 验证码创建时绑定了订单或用户，校验时必须传入相同的值；
    - 创建时没有绑定，校验时也不能凭空补一个绑定值（否则注册码可接单）。
    """
    scene = _normalize_scene(scene)
    if not captcha_id or not code:
        return False
    entry = _store.pop(captcha_id, None)
    entry_scene = _scenes.pop(captcha_id, None)
    stored_subject = _subjects.pop(captcha_id, None)
    stored_user = _users.pop(captcha_id, None)
    binding = None
    if entry_scene == "accept" and stored_subject is not None and stored_user is not None:
        binding = (stored_subject, stored_user)
        if _bindings.get(binding) == captcha_id:
            _bindings.pop(binding, None)
    if entry is None:
        return False
    expected, expire_at = entry
    if time.time() > expire_at:
        return False
    if entry_scene != scene:
        return False
    if stored_subject is not None or subject is not None:
        if stored_subject is None or subject is None or stored_subject != str(subject):
            return False
    if stored_user is not None or user_id is not None:
        if stored_user is None or user_id is None or stored_user != int(user_id):
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
