"""
API dependencies module.
FastAPI dependency injection functions for authentication and authorization.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_token
from app.db.session import get_async_session
from app.models.user import User, UserRole

# HTTP Bearer token security scheme
security = HTTPBearer(
    scheme_name="JWT",
    description="JWT Bearer token authentication",
    auto_error=False,
)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(security),
    ],
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    """
    Dependency to get the current authenticated user from JWT token.

    Extracts and validates the JWT token from the Authorization header,
    then retrieves the corresponding user from the database.

    Args:
        credentials: HTTP Bearer credentials containing the JWT token.
        db: Async database session.

    Returns:
        Authenticated User model instance.

    Raises:
        HTTPException: 401 if token is missing, invalid, or user not found.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Verify and decode token
    payload = verify_token(token, token_type="access")

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Extract user ID from token
    user_id_str = payload.get("sub")

    if user_id_str is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的令牌内容",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = int(user_id_str)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的用户标识",
            headers={"WWW-Authenticate": "Bearer"},
        ) from err

    # Fetch user from database
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用",
        )

    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """
    Dependency to ensure user is active.

    Args:
        current_user: Current authenticated user.

    Returns:
        Active User model instance.

    Raises:
        HTTPException: 403 if user is not active.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用",
        )
    return current_user


async def get_current_verified_user(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """
    Dependency to ensure user is verified.

    Args:
        current_user: Current active user.

    Returns:
        Verified User model instance.

    Raises:
        HTTPException: 403 if user is not verified.
    """
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户未验证，请先完成邮箱验证",
        )
    return current_user


def require_role(*allowed_roles: UserRole):
    """
    Factory function to create role-checking dependencies.

    Args:
        allowed_roles: Roles that are allowed to access the endpoint.

    Returns:
        Dependency function that checks user role.

    Usage:
        @router.get("/admin")
        async def admin_endpoint(
            user: User = Depends(require_role(UserRole.ADMIN))
        ):
            ...
    """
    async def role_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role not in allowed_roles:
            role_names = ", ".join(role.value for role in allowed_roles)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足，需要以下角色之一: {role_names}",
            )
        return current_user

    return role_checker


# Pre-defined role dependencies for convenience
get_current_booster = require_role(UserRole.BOOSTER, UserRole.ADMIN)
get_current_admin = require_role(UserRole.ADMIN)


async def get_optional_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(security),
    ],
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> User | None:
    """
    Dependency to optionally get the current user.
    Returns None if no valid token is provided instead of raising an exception.

    Args:
        credentials: HTTP Bearer credentials (optional).
        db: Async database session.

    Returns:
        User model instance or None.
    """
    if credentials is None:
        return None

    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


# Type aliases for cleaner dependency injection
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentActiveUser = Annotated[User, Depends(get_current_active_user)]
CurrentVerifiedUser = Annotated[User, Depends(get_current_verified_user)]
OptionalCurrentUser = Annotated[User | None, Depends(get_optional_current_user)]
DatabaseSession = Annotated[AsyncSession, Depends(get_async_session)]
