"""Administrator user management endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DatabaseSession, get_current_admin
from app.core.security import hash_password
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from app.schemas.admin_users import (
    AdminAdjustBalanceRequest,
    AdminResetPasswordRequest,
    AdminUserBalanceResponse,
    AdminUserBalanceSummary,
    AdminUserDetailResponse,
    AdminUserListResponse,
    AdminUserMessageResponse,
    AdminUserResponse,
    AdminUserStatusRequest,
    AdminUserUpdate,
)
from app.services.user_service import get_user_service
from app.services.wallet_service import get_wallet_service

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


def _summary(wallet: Wallet | None) -> AdminUserBalanceSummary:
    return AdminUserBalanceSummary(
        available=wallet.available_balance if wallet else 0,
        frozen=wallet.frozen_balance if wallet else 0,
        total_income=wallet.total_income if wallet else 0,
        total_withdrawn=wallet.total_withdrawn if wallet else 0,
    )


def _user_response(user: User, wallet: Wallet | None) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        is_verified=user.is_verified,
        created_at=user.created_at,
        booster_quota=user.booster_quota,
        wallet=_summary(wallet),
    )


async def _load_user(user_id: int, db: DatabaseSession) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return user


@router.get("", response_model=AdminUserListResponse)
async def list_admin_users(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    query: str | None = Query(None, max_length=100),
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> AdminUserListResponse:
    filters = []
    if query and query.strip():
        term = f"%{query.strip()}%"
        filters.append(or_(User.email.ilike(term), User.username.ilike(term)))
    if role is not None:
        filters.append(User.role == role)
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))

    total = int((await db.execute(select(func.count(User.id)).where(*filters))).scalar_one())
    result = await db.execute(
        select(User, Wallet).outerjoin(Wallet, Wallet.user_id == User.id)
        .where(*filters).order_by(User.created_at.desc(), User.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    items = [_user_response(user, wallet) for user, wallet in result.all()]
    pages = (total + page_size - 1) // page_size if total else 0
    return AdminUserListResponse(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.get("/{user_id}", response_model=AdminUserDetailResponse)
async def get_admin_user(
    user_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AdminUserDetailResponse:
    user = await _load_user(user_id, db)
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    return AdminUserDetailResponse(**_user_response(user, wallet).model_dump(), phone=user.phone, bio=user.bio)


@router.patch("/{user_id}", response_model=AdminUserDetailResponse)
async def update_admin_user(
    user_id: int,
    payload: AdminUserUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AdminUserDetailResponse:
    user = await _load_user(user_id, db)
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可更新的字段")
    new_username = data.get("username")
    if new_username is not None and new_username != user.username:
        duplicate = await db.execute(
            select(User.id).where(User.username == new_username, User.id != user.id)
        )
        if duplicate.scalar_one_or_none() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已被占用")
    try:
        user = await get_user_service(db).update_user(user, payload)
    except IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已被占用") from exc
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    return AdminUserDetailResponse(**_user_response(user, wallet).model_dump(), phone=user.phone, bio=user.bio)


@router.post("/{user_id}/reset-password", response_model=AdminUserMessageResponse)
async def reset_admin_user_password(
    user_id: int,
    payload: AdminResetPasswordRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AdminUserMessageResponse:
    user = await _load_user(user_id, db)
    user.hashed_password = hash_password(payload.password)
    await db.flush()
    return AdminUserMessageResponse(message="密码已重置")


@router.post("/{user_id}/status", response_model=AdminUserDetailResponse)
async def set_admin_user_status(
    user_id: int,
    payload: AdminUserStatusRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AdminUserDetailResponse:
    user = await _load_user(user_id, db)
    if user.id == current_admin.id and not payload.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能禁用自己的账户")
    user.is_active = payload.is_active
    await db.flush()
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    return AdminUserDetailResponse(**_user_response(user, wallet).model_dump(), phone=user.phone, bio=user.bio)


@router.post("/{user_id}/adjust-balance", response_model=AdminUserBalanceResponse)
async def adjust_admin_user_balance(
    user_id: int,
    payload: AdminAdjustBalanceRequest,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AdminUserBalanceResponse:
    await _load_user(user_id, db)
    wallet_service = get_wallet_service(db)
    wallet = await wallet_service.get_or_create_wallet(user_id)
    transaction = await wallet_service.admin_adjust(
        wallet, amount=payload.amount, operator_id=current_admin.id, reason=payload.reason
    )
    await db.refresh(wallet)
    return AdminUserBalanceResponse(
        available=wallet.available_balance,
        frozen=wallet.frozen_balance,
        total_income=wallet.total_income,
        total_withdrawn=wallet.total_withdrawn,
        transaction_id=transaction.id,
    )
