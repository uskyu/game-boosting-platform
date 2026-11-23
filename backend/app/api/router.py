"""
Main API router module.
Combines all endpoint routers into a single API router.
"""

from fastapi import APIRouter

from app.api.endpoints.admin_service_fee import (
    public_router as service_fee_public_router,
)
from app.api.endpoints.admin_service_fee import router as admin_service_fee_router
from app.api.endpoints import (
    admin_after_sales_router,
    admin_deposit_router,
    admin_order_analytics_router,
    admin_announcements_router,
    admin_payment_router,
    admin_platform_income_router,
    admin_publish_permission_router,
    admin_router,
    admin_site_router,
    admin_users_router,
    admin_withdrawal_category_router,
    admin_withdrawal_payout_router,
    admin_withdrawal_rule_router,
    after_sales_router,
    auth_router,
    announcements_router,
    blacklist_router,
    chat_router,
    deposit_router,
    games_router,
    leaderboard_router,
    notifications_router,
    order_hall_router,
    push_router,
    orders_router,
    order_templates_router,
    publish_permission_router,
    recharge_router,
    reviews_router,
    search_router,
    site_router,
    services_router,
    users_router,
    wallet_router,
    withdrawals_router,
)

# 直连模块导入：endpoints/__init__.py 未导出该 router（该文件不在本次改动范围内）
from app.api.endpoints.admin_order_dispute import admin_order_dispute_router
from app.api.endpoints.admin_order_analytics import (
    publisher_router as publisher_order_analytics_router,
)
from app.api.endpoints.designation import router as designation_router
from app.api.endpoints.admin_storage import router as admin_storage_router
# 登录用户的图片重新签发：预签名 URL 过期（403）后由前端 SignedImage 调用
from app.api.endpoints.storage import router as storage_router
# 通知音频自定义（四个槽位：来单/被接手/取消申请/聊天）
from app.api.endpoints.admin_sounds import router as admin_sounds_router
# 图片直传（浏览器 -> COS 预签名 PUT + 落地提交）
from app.api.endpoints.uploads import router as direct_uploads_router

# Create main API router
api_router = APIRouter()

# Include all endpoint routers
api_router.include_router(auth_router)
api_router.include_router(announcements_router)
# order_hall_router 必须早于 orders_router 注册：/orders/recent-claims
# 否则会被 /orders/{order_id}（int 路径参数）先行匹配成 422
api_router.include_router(order_hall_router)
# 指定派单候选人 /orders/designation-candidates 同理，必须先于 orders_router
api_router.include_router(designation_router)
api_router.include_router(publisher_order_analytics_router)
api_router.include_router(orders_router)
api_router.include_router(order_templates_router)
# 售后：订单维度（/orders/{id}/after-sales）+ 个人维度（/after-sales/mine）
api_router.include_router(after_sales_router)
api_router.include_router(users_router)
# 发单权限自助申请（登录用户）
api_router.include_router(publish_permission_router)
# 发单人黑名单（登录用户，ADMIN 或 can_publish=True 可用）
api_router.include_router(blacklist_router)
api_router.include_router(admin_router)
api_router.include_router(admin_after_sales_router)
api_router.include_router(admin_order_dispute_router)
api_router.include_router(admin_order_analytics_router)
api_router.include_router(admin_announcements_router)
api_router.include_router(admin_site_router)
# 对象存储设置 + 历史文件迁移/清理（仅管理员）
api_router.include_router(admin_storage_router)
# 预签名 URL 重新签发（登录用户；权限在签发时由服务端判定）
api_router.include_router(storage_router)
# 图片直传：浏览器 -> CORS 预签名 PUT + 落地提交；LOCAL 模式前端回退
api_router.include_router(direct_uploads_router)
# 通知音频自定义（上传/试听/静音/音量）
api_router.include_router(admin_sounds_router)
api_router.include_router(admin_payment_router)
api_router.include_router(admin_deposit_router)
api_router.include_router(admin_service_fee_router)
api_router.include_router(admin_platform_income_router)
# 发单页只读全局服务费设置（任何登录用户）；写操作走 admin_service_fee_router
api_router.include_router(service_fee_public_router)
api_router.include_router(admin_withdrawal_rule_router)
api_router.include_router(admin_withdrawal_category_router)
api_router.include_router(admin_withdrawal_payout_router)
api_router.include_router(admin_users_router)
# 发单权限申请审核（管理员）
api_router.include_router(admin_publish_permission_router)
api_router.include_router(chat_router)
api_router.include_router(deposit_router)
api_router.include_router(games_router)
api_router.include_router(leaderboard_router)
api_router.include_router(notifications_router)
api_router.include_router(push_router)
api_router.include_router(services_router)
api_router.include_router(search_router)
api_router.include_router(site_router)
api_router.include_router(reviews_router)
api_router.include_router(wallet_router)
api_router.include_router(recharge_router)
api_router.include_router(withdrawals_router)
