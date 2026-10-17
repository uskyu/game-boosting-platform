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
    admin_announcements_router,
    admin_payment_router,
    admin_router,
    admin_site_router,
    admin_users_router,
    admin_withdrawal_category_router,
    admin_withdrawal_payout_router,
    admin_withdrawal_rule_router,
    after_sales_router,
    auth_router,
    announcements_router,
    chat_router,
    deposit_router,
    games_router,
    notifications_router,
    order_hall_router,
    push_router,
    orders_router,
    order_templates_router,
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

# Create main API router
api_router = APIRouter()

# Include all endpoint routers
api_router.include_router(auth_router)
api_router.include_router(announcements_router)
# order_hall_router 必须早于 orders_router 注册：/orders/recent-claims
# 否则会被 /orders/{order_id}（int 路径参数）先行匹配成 422
api_router.include_router(order_hall_router)
api_router.include_router(orders_router)
api_router.include_router(order_templates_router)
# 售后：订单维度（/orders/{id}/after-sales）+ 个人维度（/after-sales/mine）
api_router.include_router(after_sales_router)
api_router.include_router(users_router)
api_router.include_router(admin_router)
api_router.include_router(admin_after_sales_router)
api_router.include_router(admin_order_dispute_router)
api_router.include_router(admin_announcements_router)
api_router.include_router(admin_site_router)
api_router.include_router(admin_payment_router)
api_router.include_router(admin_deposit_router)
api_router.include_router(admin_service_fee_router)
# 发单页只读全局服务费设置（任何登录用户）；写操作走 admin_service_fee_router
api_router.include_router(service_fee_public_router)
api_router.include_router(admin_withdrawal_rule_router)
api_router.include_router(admin_withdrawal_category_router)
api_router.include_router(admin_withdrawal_payout_router)
api_router.include_router(admin_users_router)
api_router.include_router(chat_router)
api_router.include_router(deposit_router)
api_router.include_router(games_router)
api_router.include_router(notifications_router)
api_router.include_router(push_router)
api_router.include_router(services_router)
api_router.include_router(search_router)
api_router.include_router(site_router)
api_router.include_router(reviews_router)
api_router.include_router(wallet_router)
api_router.include_router(recharge_router)
api_router.include_router(withdrawals_router)
