"""站点/安全设置的读取与懒创建。

放在 service 层是为了让认证、接单等业务代码直接读取开关，而不必反向
导入 API 端点模块（``app.api.endpoints.site``）。
"""

from sqlalchemy import select

from app.core.config import settings
from app.core.title_rules import normalize_rules
from app.models.site_setting import SiteSetting


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
