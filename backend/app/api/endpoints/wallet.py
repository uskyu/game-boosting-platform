"""
Wallet and withdrawal API endpoints.
User-facing endpoints for wallet balances, ledger history and withdrawals.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas.wallet import (
    WalletResponse,
    WalletTransactionListResponse,
    WalletTransactionResponse,
    WithdrawalCreateRequest,
    WithdrawalListResponse,
    WithdrawalResponse,
)
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
    "",
    response_model=WithdrawalResponse,
    status_code=201,
    summary="申请提现",
    description="创建提现申请并冻结对应金额（仅普通登录用户）",
)
async def create_withdrawal(
    payload: WithdrawalCreateRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> WithdrawalResponse:
    wallet_service = get_wallet_service(db)
    withdrawal = await wallet_service.create_withdrawal(
        current_user,
        amount=payload.amount,
        channel=payload.channel,
        account_name=payload.account_name,
        account_no=payload.account_no,
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
