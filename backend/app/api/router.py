"""
Main API router module.
Combines all endpoint routers into a single API router.
"""

from fastapi import APIRouter

from app.api.endpoints import (
    admin_router,
    auth_router,
    chat_router,
    games_router,
    notifications_router,
    orders_router,
    reviews_router,
    search_router,
    services_router,
    support_router,
    users_router,
    wallet_router,
    withdrawals_router,
)

# Create main API router
api_router = APIRouter()

# Include all endpoint routers
api_router.include_router(auth_router)
api_router.include_router(orders_router)
api_router.include_router(users_router)
api_router.include_router(admin_router)
api_router.include_router(chat_router)
api_router.include_router(games_router)
api_router.include_router(notifications_router)
api_router.include_router(services_router)
api_router.include_router(search_router)
api_router.include_router(reviews_router)
api_router.include_router(support_router)
api_router.include_router(wallet_router)
api_router.include_router(withdrawals_router)
