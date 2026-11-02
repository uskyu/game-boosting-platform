"""站点/安全设置的读取与懒创建。

放在 service 层是为了让认证、接单等业务代码直接读取开关，而不必反向
导入 API 端点模块（``app.api.endpoints.site``）。
"""

from sqlalchemy import select

from app.core.title_rules import normalize_rules
from app.models.site_setting import SiteSetting


async def get_or_create_site_setting(db) -> SiteSetting:
    """读取单行设置；首次访问时创建默认行。"""
    result = await db.execute(select(SiteSetting).where(SiteSetting.id == 1))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = SiteSetting(id=1, site_name="游戏服务平台")
        db.add(setting)
        await db.flush()
        await db.refresh(setting)
    return setting


async def get_order_title_replacements(db) -> list[dict[str, str]]:
    """读取后台配置的订单标题替换规则（NULL → 默认规则）。"""
    setting = await get_or_create_site_setting(db)
    return normalize_rules(setting.order_title_replacements)
