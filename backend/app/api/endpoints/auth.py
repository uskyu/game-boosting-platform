"""
Authentication API endpoints.
Handles user registration, login, and token management.
"""


from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CurrentSessionId, CurrentUser, DatabaseSession
from app.core.post_commit import run_after_commit
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
from app.services import captcha_service, user_session_service
from app.services.connection_manager import get_connection_manager
from app.services.site_setting_service import get_or_create_site_setting
from app.services.user_service import get_user_service

router = APIRouter(prefix="/auth", tags=["认证"])


class CaptchaResponse(BaseModel):
    captcha_id: str = Field(description="验证码ID")
    image: str = Field(description="base64 data URI 图片")


async def _close_revoked_sessions(user_id: int, sids: list[str]) -> None:
    """超限被淘汰/改密码被踢的设备：事务提交后关闭其 WebSocket（4002）。"""
    if not sids:
        return
    manager = get_connection_manager()
    for sid in sids:
        await run_after_commit(
            lambda s=sid: manager.close_session(
                user_id,
                s,
                user_session_service.WS_CLOSE_DEVICE_LIMIT,
                "device limit",
            )
        )


@router.get(
    "/captcha",
    response_model=CaptchaResponse,
    summary="获取图形验证码",
    description="返回验证码ID和base64图片，有效期5分钟，一次性使用；scene=register|accept",
)
async def get_captcha(
    request: Request,
    db: DatabaseSession,
    scene: str = "register",
) -> CaptchaResponse:
    import anyio
    import base64

    scene = (scene or "register").strip().lower()
    if scene not in ("register", "accept"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码场景无效",
        )

    setting = await get_or_create_site_setting(db)
    if scene == "register" and not setting.registration_captcha_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="注册验证码已关闭",
        )
    if scene == "accept" and not setting.accept_captcha_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="接单验证码已关闭",
        )

    ip = request.client.host if request.client else "unknown"
    if not captcha_service.check_rate_limit(captcha_service.scene_rate_key(scene, ip)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后再试",
        )
    # PIL 图片生成放线程池，避免注册页高频刷新时阻塞事件循环
    captcha_id, png_bytes = await anyio.to_thread.run_sync(
        captcha_service.create, scene
    )
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return CaptchaResponse(
        captcha_id=captcha_id,
        image=f"data:image/png;base64,{b64}",
    )


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
    request: Request,
) -> TokenResponse:
    """
    Register a new user account.

    - **email**: Valid email address (must be unique)
    - **username**: Display name (2-50 characters)
    - **password**: Password (minimum 6 characters)
    - Public registration always creates a USER account
    """
    user_service = get_user_service(db)

    # 注册验证码是否开启由后台控制；关闭时不校验、请求可不带验证码字段
    setting = await get_or_create_site_setting(db)
    if setting.registration_captcha_enabled:
        if not captcha_service.verify(
            user_data.captcha_id, user_data.captcha_code, "register"
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="验证码错误或已过期",
            )

    # Register user
    user = await user_service.register_user(user_data)

    # 建立设备会话；超过同时在线设备上限时淘汰最早登录的设备
    session, revoked_sids = await user_session_service.start_session(
        db,
        user,
        device_id=user_data.device_id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    await _close_revoked_sessions(user.id, revoked_sids)

    # Create tokens
    tokens = user_service.create_tokens(user, session.sid)

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
    request: Request,
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

    # 建立设备会话；超过同时在线设备上限时淘汰最早登录的设备
    session, revoked_sids = await user_session_service.start_session(
        db,
        user,
        device_id=credentials.device_id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    await _close_revoked_sessions(user.id, revoked_sids)

    # Create tokens
    tokens = user_service.create_tokens(user, session.sid)

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
    response_model=TokenResponse,
    summary="修改密码",
    description="修改当前用户的密码；本设备保持登录，其他设备登录状态立即失效",
)
async def change_password(
    password_data: PasswordChange,
    current_user: CurrentUser,
    db: DatabaseSession,
    current_sid: CurrentSessionId,
) -> TokenResponse:
    """
    Change current user's password.

    - **current_password**: Current password for verification
    - **new_password**: New password (minimum 6 characters)

    改密码会自增账号的会话版本号，使此前签发的全部令牌作废（其他设备与
    已建立的 WebSocket 一起失效）。当前设备的会话保留，并用新版本号
    重新签发一对令牌返回，客户端替换保存即可。
    """
    user_service = get_user_service(db)

    user = await user_service.change_password(
        user=current_user,
        current_password=password_data.current_password,
        new_password=password_data.new_password,
    )

    # 只吊销其他设备的会话；当前设备保留，不掉线。
    revoked_sids = await user_session_service.revoke_others(
        db, user.id, keep_sid=current_sid, reason="password_changed"
    )
    await _close_revoked_sessions(user.id, revoked_sids)

    tokens = user_service.create_tokens(user, current_sid)

    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        expires_in=tokens["expires_in"],
        user=UserResponse.model_validate(user),
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="用户登出",
    description="登出当前用户（客户端应删除令牌）",
)
async def logout(
    current_user: CurrentUser,
    db: DatabaseSession,
    current_sid: CurrentSessionId,
) -> MessageResponse:
    """
    Logout current user.

    吊销当前设备的会话（该 token 立即失效），并断开对应 WebSocket。
    """
    if current_sid:
        await user_session_service.revoke_session(db, current_sid, "logout")
        manager = get_connection_manager()
        await run_after_commit(
            lambda: manager.close_session(
                current_user.id, current_sid, 4001, "logout"
            )
        )
    return MessageResponse(message="登出成功", success=True)
