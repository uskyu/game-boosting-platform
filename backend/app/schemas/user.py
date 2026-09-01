"""
User and authentication schemas module.
Pydantic models for user-related API request/response validation.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole

# =============================================================================
# INPUT SCHEMAS (Request Bodies)
# =============================================================================

class UserRegister(BaseModel):
    """Schema for user registration."""

    email: EmailStr = Field(
        ...,
        description="邮箱地址",
        examples=["user@example.com"],
    )

    username: str = Field(
        ...,
        min_length=2,
        max_length=50,
        description="用户名",
        examples=["玩家小明"],
    )

    password: str = Field(
        ...,
        min_length=8,
        max_length=100,
        description="密码（至少8位，须包含大写字母和数字）",
        examples=["SecurePass123"],
    )

    role: UserRole = Field(
        default=UserRole.USER,
        description="用户角色",
    )

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Validate username format."""
        v = v.strip()
        if not v:
            raise ValueError("用户名不能为空")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError("密码长度至少8位")
        if not any(c.isupper() for c in v):
            raise ValueError("密码须包含至少一个大写字母")
        if not any(c.isdigit() for c in v):
            raise ValueError("密码须包含至少一个数字")
        return v


class UserLogin(BaseModel):
    """Schema for user login."""

    email: EmailStr = Field(
        ...,
        description="邮箱地址",
        examples=["user@example.com"],
    )

    password: str = Field(
        ...,
        min_length=1,
        description="密码",
    )


class UserUpdate(BaseModel):
    """Schema for updating user profile."""

    username: str | None = Field(
        default=None,
        min_length=2,
        max_length=50,
        description="用户名",
    )

    avatar_url: str | None = Field(
        default=None,
        max_length=500,
        description="头像URL",
    )

    phone: str | None = Field(
        default=None,
        max_length=20,
        description="手机号",
    )

    bio: str | None = Field(
        default=None,
        max_length=500,
        description="个人简介",
    )


class PasswordChange(BaseModel):
    """Schema for changing password."""

    current_password: str = Field(
        ...,
        min_length=1,
        description="当前密码",
    )

    new_password: str = Field(
        ...,
        min_length=8,
        max_length=100,
        description="新密码（至少8位，须包含大写字母和数字）",
    )

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        """Validate new password strength."""
        if len(v) < 8:
            raise ValueError("新密码长度至少8位")
        if not any(c.isupper() for c in v):
            raise ValueError("新密码须包含至少一个大写字母")
        if not any(c.isdigit() for c in v):
            raise ValueError("新密码须包含至少一个数字")
        return v


# =============================================================================
# OUTPUT SCHEMAS (Response Bodies)
# =============================================================================

class UserResponse(BaseModel):
    """User response schema."""

    id: int = Field(description="用户ID")
    email: str = Field(description="邮箱地址")
    username: str = Field(description="用户名")
    role: UserRole = Field(description="用户角色")
    is_active: bool = Field(description="是否激活")
    is_verified: bool = Field(description="是否验证")
    avatar_url: str | None = Field(default=None, description="头像URL")
    phone: str | None = Field(default=None, description="手机号")
    bio: str | None = Field(default=None, description="个人简介")
    created_at: datetime = Field(description="注册时间")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "email": "user@example.com",
                "username": "玩家小明",
                "role": "USER",
                "is_active": True,
                "is_verified": False,
                "avatar_url": None,
                "phone": None,
                "bio": None,
                "created_at": "2024-01-01T00:00:00",
            }
        }
    )


class BoosterProfileResponse(BaseModel):
    """Public booster profile with reputation data."""

    id: int = Field(description="用户ID")
    username: str = Field(description="用户名")
    avatar_url: str | None = Field(default=None, description="头像URL")
    bio: str | None = Field(default=None, description="个人简介")
    created_at: datetime = Field(description="注册时间")

    credit_score: int = Field(description="信誉分")
    credit_level: str = Field(description="信誉等级")
    total_completed: int = Field(description="完成订单数")
    total_disputed: int = Field(description="争议次数")
    completion_rate: float = Field(description="完成率")
    avg_rating: float = Field(description="平均评分")
    avg_response_minutes: int = Field(description="平均响应时间(分钟)")
    badge_tags: list[str] = Field(default_factory=list, description="标签徽章")

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    """JWT token response schema."""

    access_token: str = Field(description="访问令牌")
    refresh_token: str = Field(description="刷新令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    expires_in: int = Field(description="过期时间(秒)")
    user: UserResponse = Field(description="用户信息")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 1800,
                "user": {
                    "id": 1,
                    "email": "user@example.com",
                    "username": "玩家小明",
                    "role": "USER",
                    "is_active": True,
                    "is_verified": False,
                    "avatar_url": None,
                    "phone": None,
                    "bio": None,
                    "created_at": "2024-01-01T00:00:00",
                },
            }
        }
    )


class TokenRefresh(BaseModel):
    """Schema for refreshing access token."""

    refresh_token: str = Field(
        ...,
        description="刷新令牌",
    )


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str = Field(description="消息内容")
    success: bool = Field(default=True, description="是否成功")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "操作成功",
                "success": True,
            }
        }
    )
