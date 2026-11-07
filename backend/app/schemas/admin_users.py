"""Schemas for administrator user management."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.models.user import UserRole
from app.schemas.serializers import serialize_datetime_utc


class AdminUserBalanceSummary(BaseModel):
    """Wallet balances shown in administrator user views."""

    available: Decimal
    frozen: Decimal
    deposit_balance: Decimal
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
    can_publish: bool = True
    can_accept: bool = True
    created_at: datetime
    booster_quota: int
    wallet: AdminUserBalanceSummary

    @field_serializer("created_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return serialize_datetime_utc(value)

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
    """Fields an administrator may edit; email is intentionally absent.

    role 仅支持两态：ADMIN（管理员）/ BOOSTER（打手，即普通接单用户）。
    """

    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=2, max_length=50)
    role: UserRole | None = None
    phone: str | None = Field(default=None, max_length=20)
    bio: str | None = Field(default=None, max_length=500)
    is_verified: bool | None = None
    booster_quota: int | None = Field(default=None, ge=0, le=50)
    can_publish: bool | None = None
    can_accept: bool | None = None

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


class AdminUserRestrictionRequest(BaseModel):
    """Per-action restrictions, decoupled from global is_active."""

    model_config = ConfigDict(extra="forbid")

    can_publish: bool | None = None
    can_accept: bool | None = None
    reason: str | None = Field(default=None, max_length=500)


class BulkPublishPermissionRequest(BaseModel):
    """批量调整发单权限：勾选用户或全部非管理员（不含已禁用账号）。"""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["selected", "all_non_admin"]
    user_ids: list[int] | None = None
    can_publish: bool

    @model_validator(mode="after")
    def validate_selected_scope(self):
        if self.scope == "selected":
            if not self.user_ids:
                raise ValueError("请选择至少一个用户")
            if len(self.user_ids) > 200:
                raise ValueError("一次最多调整 200 个用户")
        return self


class BulkPublishPermissionResponse(BaseModel):
    updated: int
    skipped: int


class BulkPublishPermissionPreviewResponse(BaseModel):
    """全量批量调整发单权限的影响人数。"""

    eligible: int
    pending: int


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
