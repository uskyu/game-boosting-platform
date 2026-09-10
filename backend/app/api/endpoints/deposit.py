"""保证金用户侧接口：查看权益、余额转入保证金、保证金转回余额。"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DatabaseSession
from app.models.user import UserRole
from app.schemas.deposit import (
    DepositOverviewResponse,
    DepositTierResponse,
    DepositTransferRequest,
)
from app.services import deposit_service
from app.services.wallet_service import get_wallet_service

router = APIRouter(prefix="/wallet/deposit", tags=["保证金"])


def _reject_admin(user) -> None:
    """管理员不参与打手保证金体系（与提现一致）。"""
    if user.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员账号无需缴纳保证金",
        )


@router.get(
    "",
    response_model=DepositOverviewResponse,
    summary="我的保证金",
    description="返回保证金余额、当前命中档位权益与全部生效阶梯，以及能否转回余额。",
)
async def get_my_deposit(
    current_user: CurrentUser,
    db: DatabaseSession,
) -> DepositOverviewResponse:
    _reject_admin(current_user)

    wallet_service = get_wallet_service(db)
    wallet = await wallet_service.get_or_create_wallet(current_user.id)
    tiers = await deposit_service.list_tiers(db)
    balance = wallet.deposit_balance
    tier = deposit_service.resolve_tier(tiers, balance)
    block_reason = await deposit_service.return_block_reason(db, current_user.id)
    enabled = await deposit_service.is_deposit_enabled(db)

    return DepositOverviewResponse(
        enabled=enabled,
        deposit_balance=balance,
        available_balance=wallet.available_balance,
        current_threshold=tier.threshold if tier else None,
        wait_seconds=tier.wait_seconds if tier else 0,
        exempt_compensation=tier.exempt_compensation if tier else False,
        settle_hours=tier.settle_hours if tier else 72,
        # 总开关关闭时不再提供转入（由 service 拒绝），但保留转回入口避免锁死资金
        can_return=block_reason is None and balance > 0,
        return_block_reason=block_reason,
        tiers=[DepositTierResponse.model_validate(t) for t in tiers],
    )


@router.post(
    "/in",
    status_code=status.HTTP_201_CREATED,
    summary="缴纳保证金",
    description="从可用余额转入保证金；转入后自动冻结，不可消费、不可提现。",
)
async def transfer_in_deposit(
    payload: DepositTransferRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> dict:
    _reject_admin(current_user)
    await deposit_service.transfer_in(db, current_user, payload.amount)
    return {"message": "保证金缴纳成功", "success": True}


@router.post(
    "/out",
    status_code=status.HTTP_201_CREATED,
    summary="保证金转回余额",
    description="把保证金转回可用余额；须满足全部订单完成满 7 天的冷却期。",
)
async def transfer_out_deposit(
    payload: DepositTransferRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> dict:
    _reject_admin(current_user)
    await deposit_service.transfer_out(db, current_user, payload.amount)
    return {"message": "保证金已转回余额", "success": True}
