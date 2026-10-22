"""站点/安全设置的读取与懒创建。

放在 service 层是为了让认证、接单等业务代码直接读取开关，而不必反向
导入 API 端点模块（``app.api.endpoints.site``）。
"""

from sqlalchemy import select

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
