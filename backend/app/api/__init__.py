"""
API package.
Contains all API routes, dependencies, and endpoint handlers.
"""

from app.api.deps import (
    CurrentActiveUser,
    CurrentUser,
    CurrentVerifiedUser,
    DatabaseSession,
    OptionalCurrentUser,
    get_current_active_user,
    get_current_admin,
    get_current_booster,
    get_current_user,
    get_current_verified_user,
    get_optional_current_user,
    require_role,
)
from app.api.router import api_router

__all__ = [
    "CurrentActiveUser",
    "CurrentUser",
    "CurrentVerifiedUser",
    "DatabaseSession",
    "OptionalCurrentUser",
    "api_router",
    "get_current_active_user",
    "get_current_admin",
    "get_current_booster",
    "get_current_user",
    "get_current_verified_user",
    "get_optional_current_user",
    "require_role",
]
