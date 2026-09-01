"""
API endpoints package.
Contains all API route handlers organized by resource.
"""

from app.api.endpoints.admin import router as admin_router
from app.api.endpoints.admin_site import router as admin_site_router
from app.api.endpoints.admin_users import router as admin_users_router
from app.api.endpoints.auth import router as auth_router
from app.api.endpoints.chat import router as chat_router
from app.api.endpoints.games import router as games_router
from app.api.endpoints.notifications import router as notifications_router
from app.api.endpoints.orders import router as orders_router
from app.api.endpoints.reviews import router as reviews_router
from app.api.endpoints.search import router as search_router
from app.api.endpoints.site import router as site_router
from app.api.endpoints.services import router as services_router
from app.api.endpoints.support import router as support_router
from app.api.endpoints.users import router as users_router
from app.api.endpoints.wallet import router as wallet_router, withdrawals_router

__all__ = [
    "admin_router",
    "admin_site_router",
    "admin_users_router",
    "auth_router",
    "chat_router",
    "games_router",
    "notifications_router",
    "orders_router",
    "reviews_router",
    "search_router",
    "site_router",
    "services_router",
    "support_router",
    "users_router",
    "wallet_router",
    "withdrawals_router",
]
