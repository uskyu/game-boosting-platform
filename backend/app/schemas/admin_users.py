"""Schemas for administrator user management."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.user import UserRole


class AdminUserBalanceSummary(BaseModel):
    """Wallet balances shown in administrator user views."""

    available: Decimal
    frozen: Decimal
    total_income: Decimal
    total_withdrawn: Decimal


class AdminUserResponse(BaseModel):
    """Administrator-facing user record."""

    id: int
    email: str
    username: str
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime
    booster_quota: int
    wallet: AdminUserBalanceSummary

    model_config = ConfigDict(from_attributes=True)


class AdminUserListResponse(BaseModel):
    items: list[AdminUserResponse]
    total: int
    page: int
    page_size: int
    pages: int


class AdminUserDetailResponse(AdminUserResponse):
    """Detailed administrator-facing user record."""

    phone: str | None = None
    bio: str | None = None


class AdminUserUpdate(BaseModel):
    """Fields an administrator may edit; email and role are intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=2, max_length=50)
    phone: str | None = Field(default=None, max_length=20)
    bio: str | None = Field(default=None, max_length=500)
    is_verified: bool | None = None
    booster_quota: int | None = Field(default=None, ge=0, le=50)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("用户名不能为空")
        return value


class AdminResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(..., min_length=8, max_length=100)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not any(char.isupper() for char in value):
            raise ValueError("密码须包含至少一个大写字母")
        if not any(char.isdigit() for char in value):
            raise ValueError("密码须包含至少一个数字")
        return value


class AdminUserStatusRequest(BaseModel):
    is_active: bool


class AdminAdjustBalanceRequest(BaseModel):
    amount: Decimal
    reason: str = Field(..., min_length=1, max_length=255)

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: object) -> Decimal:
        amount = Decimal(str(value).replace("¥", "").replace("元", "").replace(",", "").strip())
        if not amount.is_finite():
            raise ValueError("调整金额必须是有效数字")
        return amount


class AdminUserMessageResponse(BaseModel):
    message: str
    success: bool = True


class AdminUserBalanceResponse(AdminUserBalanceSummary):
    """Balance returned after an administrator adjustment."""

    transaction_id: int
