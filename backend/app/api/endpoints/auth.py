"""
Authentication API endpoints.
Handles user registration, login, and token management.
"""


from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas.user import (
    MessageResponse,
    PasswordChange,
    TokenRefresh,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
    UserUpdate,
)
from app.services.user_service import get_user_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="用户注册",
    description="注册新用户账户，返回JWT令牌",
)
async def register(
    user_data: UserRegister,
    db: DatabaseSession,
) -> TokenResponse:
    """
    Register a new user account.

    - **email**: Valid email address (must be unique)
    - **username**: Display name (2-50 characters)
    - **password**: Password (minimum 6 characters)
    - Public registration always creates a USER account
    """
    user_service = get_user_service(db)

    # Register user
    user = await user_service.register_user(user_data)

    # Create tokens
    tokens = user_service.create_tokens(user)

    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        expires_in=tokens["expires_in"],
        user=UserResponse.model_validate(user),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="用户登录",
    description="使用邮箱和密码登录，返回JWT令牌",
)
async def login(
    credentials: UserLogin,
    db: DatabaseSession,
) -> TokenResponse:
    """
    Authenticate user and return JWT tokens.

    - **email**: Registered email address
    - **password**: Account password
    """
    user_service = get_user_service(db)

    # Authenticate user
    user = await user_service.authenticate_user(
        email=credentials.email,
        password=credentials.password,
    )

    # Create tokens
    tokens = user_service.create_tokens(user)

    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        expires_in=tokens["expires_in"],
        user=UserResponse.model_validate(user),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="刷新令牌",
    description="使用刷新令牌获取新的访问令牌",
)
async def refresh_token(
    token_data: TokenRefresh,
    db: DatabaseSession,
) -> TokenResponse:
    """
    Refresh access token using refresh token.

    - **refresh_token**: Valid refresh token
    """
    user_service = get_user_service(db)

    # Refresh tokens
    tokens = await user_service.refresh_access_token(token_data.refresh_token)

    # Get user for response
    from app.core.security import verify_token
    payload = verify_token(tokens["access_token"], token_type="access")
    user = await user_service.get_user_by_id(int(payload["sub"]))

    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        expires_in=tokens["expires_in"],
        user=UserResponse.model_validate(user),
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="获取当前用户",
    description="获取当前登录用户的信息",
)
async def get_current_user_info(
    current_user: CurrentUser,
) -> UserResponse:
    """
    Get current authenticated user information.

    Requires valid JWT token in Authorization header.
    """
    return UserResponse.model_validate(current_user)


@router.put(
    "/me",
    response_model=UserResponse,
    summary="更新用户信息",
    description="更新当前用户的个人信息",
)
async def update_current_user(
    update_data: UserUpdate,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> UserResponse:
    """
    Update current user profile.

    - **username**: New display name (optional)
    - **avatar_url**: Avatar image URL (optional)
    - **phone**: Phone number (optional)
    - **bio**: Personal biography (optional)
    """
    user_service = get_user_service(db)

    updated_user = await user_service.update_user(current_user, update_data)

    return UserResponse.model_validate(updated_user)


@router.post(
    "/change-password",
    response_model=MessageResponse,
    summary="修改密码",
    description="修改当前用户的密码",
)
async def change_password(
    password_data: PasswordChange,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> MessageResponse:
    """
    Change current user's password.

    - **current_password**: Current password for verification
    - **new_password**: New password (minimum 6 characters)
    """
    user_service = get_user_service(db)

    await user_service.change_password(
        user=current_user,
        current_password=password_data.current_password,
        new_password=password_data.new_password,
    )

    return MessageResponse(message="密码修改成功", success=True)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="用户登出",
    description="登出当前用户（客户端应删除令牌）",
)
async def logout(
    current_user: CurrentUser,
) -> MessageResponse:
    """
    Logout current user.

    Note: JWT tokens are stateless, so this endpoint just returns success.
    Client should remove stored tokens.
    """
    return MessageResponse(message="登出成功", success=True)
