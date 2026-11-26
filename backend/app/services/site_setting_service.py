"""站点/安全设置的读取与懒创建。

放在 service 层是为了让认证、接单等业务代码直接读取开关，而不必反向
导入 API 端点模块（``app.api.endpoints.site``）。
"""

import time

from sqlalchemy import select

from app.core.config import settings
from app.core.title_rules import normalize_rules
from app.models.site_setting import SiteSetting
from app.schemas.notification_sound import normalize_sounds

# 大厅排序模式的进程内短缓存（2s）。大厅每个客户端每 2-10 秒就会拉一次
# 列表/摘要，如果每次都回表读这一行设置，等于把一条静态配置变成高频查询。
# 管理员保存时由 admin 端点主动 invalidate_hall_sort_cache()，同一进程内
# 立即生效；这个 2 秒 TTL 只兜住「假设存在多个进程」时其他进程的可见窗口，
# 避免老板反馈的「改了排序要等 1-2 分钟才看到」（旧值 15s 叠加客户端轮询
# 间隔，体感上是分钟级）。
_HALL_SORT_CACHE = {"value": "NEWEST", "expires_at": 0.0}
_HALL_SORT_TTL_SECONDS = 2.0


async def ensure_site_settings(db) -> SiteSetting:
    """首次部署初始化单行站点设置；已存在则完全不动。

    新站可以在 .env 里把 ``REGISTRATION_DEFAULT_CAN_PUBLISH`` 等设为 false，
    启动时写入初始行；之后管理员在后台改值，重启不会被 env 重新覆盖。
    表未迁移时抛出 SQLAlchemyError，由调用方（启动 lifespan）捕获并 warning。
    """
    result = await db.execute(select(SiteSetting).where(SiteSetting.id == 1))
    setting = result.scalar_one_or_none()
    if setting is not None:
        return setting

    setting = SiteSetting(
        id=1,
        site_name="游戏服务平台",
        registration_default_can_publish=settings.REGISTRATION_DEFAULT_CAN_PUBLISH,
        registration_default_can_accept=settings.REGISTRATION_DEFAULT_CAN_ACCEPT,
        publish_permission_apply_enabled=settings.PUBLISH_PERMISSION_APPLY_ENABLED,
        publish_permission_required=settings.PUBLISH_PERMISSION_REQUIRED,
    )
    db.add(setting)
    await db.flush()
    await db.refresh(setting)
    return setting


async def get_or_create_site_setting(db) -> SiteSetting:
    """读取单行设置；首次访问时按 env 默认值创建。"""
    return await ensure_site_settings(db)


async def get_order_title_replacements(db) -> list[dict[str, str]]:
    """读取后台配置的订单标题替换规则（NULL → 默认规则）。"""
    setting = await get_or_create_site_setting(db)
    return normalize_rules(setting.order_title_replacements)


async def get_hall_sort_mode(db) -> str:
    """返回站点大厅排序模式（"NEWEST" / "OLDEST"），带 2 秒进程内缓存。

    大厅每个客户端每 2-10 秒拉一次列表/摘要，为一行静态配置每次都回表
    是纯浪费；这里只读单个标量列，命中缓存时零 DB 往返。管理员保存设置
    会调用 invalidate_hall_sort_cache() 立即失效，同进程内不必等 TTL。

    设置行缺失（首启尚未懒创建）时返回 "NEWEST"，与列默认值一致。
    迁移在应用启动前执行，因此不需要处理「列不存在」的情况。
    """
    now = time.monotonic()
    if now < _HALL_SORT_CACHE["expires_at"]:
        return _HALL_SORT_CACHE["value"]

    value = (
        await db.execute(
            select(SiteSetting.hall_sort_mode).where(SiteSetting.id == 1)
        )
    ).scalar_one_or_none()
    if value not in ("NEWEST", "OLDEST"):
        value = "NEWEST"
    _HALL_SORT_CACHE["value"] = value
    _HALL_SORT_CACHE["expires_at"] = now + _HALL_SORT_TTL_SECONDS
    return value


def invalidate_hall_sort_cache() -> None:
    """立即让大厅排序模式缓存失效（管理员保存设置后调用）。"""
    _HALL_SORT_CACHE["expires_at"] = 0.0


async def get_notification_sounds(db) -> dict:
    """返回归一化后的通知音频配置（缺失槽位用内置默认）。"""
    setting = await get_or_create_site_setting(db)
    return normalize_sounds(setting.notification_sounds)


LOGIN_SESSION_DAYS_MIN = 1
LOGIN_SESSION_DAYS_MAX = 365


def normalize_login_session_days(value) -> int:
    """返回合法的「登录保持天数」。

    非法/缺失（None、非数字、<1、>365）回退到 env 默认
    ``REFRESH_TOKEN_EXPIRE_DAYS``，再夹取到 1..365，保证签发令牌时不会
    出现 0 天或超长有效期。
    """
    try:
        fallback = int(settings.REFRESH_TOKEN_EXPIRE_DAYS)
    except (TypeError, ValueError):
        fallback = 7
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = fallback
    return min(max(days, LOGIN_SESSION_DAYS_MIN), LOGIN_SESSION_DAYS_MAX)


async def get_login_session_days(db) -> int:
    """读取后台配置的登录保持天数（刷新令牌/设备会话有效期）。"""
    setting = await get_or_create_site_setting(db)
    return normalize_login_session_days(setting.login_session_days)
