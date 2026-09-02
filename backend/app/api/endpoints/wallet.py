"""
Wallet and withdrawal API endpoints.
User-facing endpoints for wallet balances, ledger history and withdrawals.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from app.api.deps import CurrentUser, DatabaseSession
from app.models.user import UserRole
from app.schemas.wallet import (
    WalletResponse,
    WalletTransactionListResponse,
    WalletTransactionResponse,
    WithdrawalCreateRequest,
    WithdrawalListResponse,
    WithdrawalQrcodeUploadResponse,
    WithdrawalResponse,
)
from app.services.file_service import save_image_bytes, validate_image_upload
from app.services.wallet_service import get_wallet_service

router = APIRouter(prefix="/wallet", tags=["钱包"])

withdrawals_router = APIRouter(prefix="/withdrawals", tags=["提现"])


def _pages(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size if total > 0 else 0


@router.get(
    "",
    response_model=WalletResponse,
    summary="获取我的钱包",
    description="获取当前登录用户的钱包余额概览",
)
async def get_my_wallet(
    current_user: CurrentUser,
    db: DatabaseSession,
) -> WalletResponse:
    wallet_service = get_wallet_service(db)
    wallet = await wallet_service.get_or_create_wallet(current_user.id)
    return WalletResponse.model_validate(wallet)


@router.get(
    "/transactions",
    response_model=WalletTransactionListResponse,
    summary="获取钱包流水",
    description="分页获取当前登录用户的钱包流水（时间倒序）",
)
async def list_my_transactions(
    current_user: CurrentUser,
    db: DatabaseSession,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
) -> WalletTransactionListResponse:
    wallet_service = get_wallet_service(db)
    transactions, total = await wallet_service.list_transactions(
        current_user.id,
        page=page,
        page_size=page_size,
    )
    return WalletTransactionListResponse(
        items=[WalletTransactionResponse.model_validate(tx) for tx in transactions],
        total=total,
        page=page,
        page_size=page_size,
        pages=_pages(total, page_size),
    )


@withdrawals_router.post(
    "/qrcode",
    response_model=WithdrawalQrcodeUploadResponse,
    status_code=201,
    summary="上传收款二维码",
    description="上传提现收款二维码图片（PNG/JPEG/WebP，<=10MB），保存到当前用户专属目录",
)
async def upload_withdrawal_qrcode(
    current_user: CurrentUser,
    file: UploadFile = File(..., description="二维码图片"),
) -> WithdrawalQrcodeUploadResponse:
    """
    Upload a payment QR code image for withdrawals.

    - Any logged-in non-admin account
    - PNG/JPEG/WebP up to 10MB, magic bytes verified
    - Stored under uploads/withdrawals/{user_id}/ (per-user isolation);
      the returned URL must be referenced by the subsequent withdrawal
      create request
    """
    if current_user.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员无需上传提现二维码",
        )

    data, suffix = await validate_image_upload(file)
    name = Path(file.filename or "qrcode").name
    if not name or len(name) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件名无效",
        )
    url = save_image_bytes(data, suffix, f"withdrawals/{current_user.id}")
    return WithdrawalQrcodeUploadResponse(
        url=url,
        name=name,
        size=len(data),
        content_type=(file.content_type or "").lower(),
    )


def _validate_qrcode_url(qrcode_url: str, user_id: int) -> str:
    """A withdrawal QR code must live in the applicant's own upload folder."""
    prefix = f"/uploads/withdrawals/{user_id}/"
    if not qrcode_url.startswith(prefix) or len(qrcode_url) <= len(prefix):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="二维码图片路径无效",
        )
    filename = qrcode_url[len(prefix):]
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="二维码图片路径无效",
        )
    return qrcode_url


@withdrawals_router.post(
    "",
    response_model=WithdrawalResponse,
    status_code=201,
    summary="申请提现",
    description="创建提现申请并冻结对应金额（仅普通登录用户）；可携带 qrcode_url 收款二维码",
)
async def create_withdrawal(
    payload: WithdrawalCreateRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> WithdrawalResponse:
    qrcode_url = payload.qrcode_url
    if qrcode_url is not None:
        qrcode_url = _validate_qrcode_url(qrcode_url, current_user.id)

    wallet_service = get_wallet_service(db)
    withdrawal = await wallet_service.create_withdrawal(
        current_user,
        amount=payload.amount,
        channel=payload.channel,
        account_name=payload.account_name,
        account_no=payload.account_no,
        qrcode_url=qrcode_url,
    )
    return WithdrawalResponse.model_validate(withdrawal)


@withdrawals_router.get(
    "/mine",
    response_model=WithdrawalListResponse,
    summary="我的提现记录",
    description="分页获取当前登录用户的提现记录（时间倒序）",
)
async def list_my_withdrawals(
    current_user: CurrentUser,
    db: DatabaseSession,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
) -> WithdrawalListResponse:
    wallet_service = get_wallet_service(db)
    withdrawals, total = await wallet_service.list_withdrawals(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
    )
    return WithdrawalListResponse(
        items=[WithdrawalResponse.model_validate(w) for w in withdrawals],
        total=total,
        page=page,
        page_size=page_size,
        pages=_pages(total, page_size),
    )
