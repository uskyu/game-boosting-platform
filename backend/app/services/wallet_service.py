"""
Wallet service module.
Business logic for wallet balances, ledger entries and withdrawals.

All balance mutations go through WalletService so every change:
1. Locks the wallet row with SELECT ... FOR UPDATE (serializes concurrent
   mutations on the same wallet).
2. Computes balance_before / balance_after.
3. Inserts a ledger row into wallet_transactions.
4. Updates the wallet balances.

Amounts are always decimal.Decimal - never float.

Sign convention for WalletTransaction.amount:
- positive = money entering the wallet (available balance increases),
- negative = money leaving the wallet (available balance decreases).
balance_before / balance_after always snapshot available_balance.
"""

import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.order import Order
from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from app.models.withdrawal import WithdrawalRequest, WithdrawalStatus

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")


def _to_decimal(value: Decimal | int | str) -> Decimal:
    """Coerce incoming amounts to Decimal (never float)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class WalletService:
    """
    Service class for wallet and withdrawal business logic.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Wallet lookup
    # ------------------------------------------------------------------

    async def get_or_create_wallet(self, user_id: int) -> Wallet:
        """
        Return the wallet for a user, creating it (zero balances) on demand.

        Safe against concurrent creation: a duplicate insert raises a
        unique-key error inside a savepoint, which we roll back and then
        re-select the row created by the winning transaction.
        """
        result = await self._db.execute(
            select(Wallet).where(Wallet.user_id == user_id)
        )
        wallet = result.scalar_one_or_none()
        if wallet is not None:
            return wallet

        try:
            async with self._db.begin_nested():
                wallet = Wallet(
                    user_id=user_id,
                    available_balance=_ZERO,
                    frozen_balance=_ZERO,
                    total_income=_ZERO,
                    total_withdrawn=_ZERO,
                )
                self._db.add(wallet)
                await self._db.flush()
            return wallet
        except IntegrityError:
            result = await self._db.execute(
                select(Wallet).where(Wallet.user_id == user_id)
            )
            wallet = result.scalar_one()
            return wallet

    async def _lock_wallet(self, wallet_id: int) -> Wallet:
        """Re-select the wallet row with FOR UPDATE inside the current transaction."""
        result = await self._db.execute(
            select(Wallet).where(Wallet.id == wallet_id).with_for_update()
        )
        wallet = result.scalar_one()
        return wallet

    # ------------------------------------------------------------------
    # Core mutation primitive
    # ------------------------------------------------------------------

    async def _apply(
        self,
        wallet: Wallet,
        *,
        tx_type: WalletTransactionType,
        amount: Decimal,
        available_delta: Decimal,
        order_id: int | None = None,
        booster_id: int | None = None,
        withdrawal_id: int | None = None,
        operator_id: int | None = None,
        remark: str | None = None,
        income_delta: Decimal = _ZERO,
        frozen_delta: Decimal = _ZERO,
        withdrawn_delta: Decimal = _ZERO,
    ) -> WalletTransaction:
        """
        Single mutation primitive.

        Locks the wallet row, appends the ledger row and applies deltas to
        available_balance / frozen_balance / total_income / total_withdrawn.

        Args:
            wallet: Wallet to mutate (re-locked inside).
            tx_type: Ledger entry type.
            amount: Signed amount recorded in the ledger.
            available_delta: Actual change to available_balance (differs from
                ``amount`` for WITHDRAWAL_PAID, which deducts frozen balance
                and leaves available unchanged).
            order_id / booster_id / withdrawal_id / operator_id / remark:
                ledger context. booster_id records which booster an order
                settlement belongs to (multi-claim orders settle per booster).
            income_delta: added to total_income (default 0).
            frozen_delta: added to frozen_balance (default 0).
            withdrawn_delta: added to total_withdrawn (default 0).
        """
        wallet = await self._lock_wallet(wallet.id)

        balance_before = _to_decimal(wallet.available_balance)
        balance_after = balance_before + available_delta

        transaction = WalletTransaction(
            wallet_id=wallet.id,
            type=tx_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            order_id=order_id,
            booster_id=booster_id,
            withdrawal_id=withdrawal_id,
            operator_id=operator_id,
            remark=remark,
        )
        self._db.add(transaction)

        wallet.available_balance = balance_after
        if frozen_delta:
            wallet.frozen_balance = _to_decimal(wallet.frozen_balance) + frozen_delta
        if income_delta:
            wallet.total_income = _to_decimal(wallet.total_income) + income_delta
        if withdrawn_delta:
            wallet.total_withdrawn = (
                _to_decimal(wallet.total_withdrawn) + withdrawn_delta
            )
        wallet.updated_at = datetime.now(timezone.utc)

        await self._db.flush()
        await self._db.refresh(transaction)
        return transaction

    # ------------------------------------------------------------------
    # Public balance operations
    # ------------------------------------------------------------------

    async def credit(
        self,
        wallet: Wallet,
        *,
        amount: Decimal,
        tx_type: WalletTransactionType,
        order_id: int | None = None,
        booster_id: int | None = None,
        operator_id: int | None = None,
        remark: str | None = None,
    ) -> WalletTransaction:
        """
        Credit money into available balance. Accumulates total_income when
        the ledger type is ORDER_INCOME. amount must be positive.
        """
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount <= _ZERO:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="入账金额必须大于0",
            )
        return await self._apply(
            wallet,
            tx_type=tx_type,
            amount=amount,
            available_delta=amount,
            order_id=order_id,
            booster_id=booster_id,
            operator_id=operator_id,
            remark=remark,
            income_delta=amount if tx_type == WalletTransactionType.ORDER_INCOME else _ZERO,
        )

    async def freeze(
        self,
        wallet: Wallet,
        *,
        amount: Decimal,
        withdrawal_id: int,
        remark: str | None = None,
    ) -> WalletTransaction:
        """Move amount from available to frozen (withdrawal submission)."""
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount <= _ZERO:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="冻结金额必须大于0",
            )
        if _to_decimal(wallet.available_balance) < amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="可用余额不足",
            )
        return await self._apply(
            wallet,
            tx_type=WalletTransactionType.WITHDRAWAL_FREEZE,
            amount=-amount,
            available_delta=-amount,
            withdrawal_id=withdrawal_id,
            remark=remark or "提现申请冻结",
            frozen_delta=amount,
        )

    async def unfreeze(
        self,
        wallet: Wallet,
        *,
        amount: Decimal,
        withdrawal_id: int,
        remark: str | None = None,
    ) -> WalletTransaction:
        """Move amount from frozen back to available (withdrawal rejection)."""
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount <= _ZERO:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="解冻金额必须大于0",
            )
        if _to_decimal(wallet.frozen_balance) < amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="冻结余额不足",
            )
        return await self._apply(
            wallet,
            tx_type=WalletTransactionType.WITHDRAWAL_REFUND,
            amount=amount,
            available_delta=amount,
            withdrawal_id=withdrawal_id,
            remark=remark or "提现驳回解冻",
            frozen_delta=-amount,
        )

    async def settle_withdrawal(
        self,
        wallet: Wallet,
        *,
        amount: Decimal,
        withdrawal_id: int,
        remark: str | None = None,
    ) -> WalletTransaction:
        """
        Complete a withdrawal: deduct frozen balance and accumulate
        total_withdrawn (admin marked the payout as paid).
        """
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount <= _ZERO:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="打款金额必须大于0",
            )
        if _to_decimal(wallet.frozen_balance) < amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="冻结余额不足",
            )
        return await self._apply(
            wallet,
            tx_type=WalletTransactionType.WITHDRAWAL_PAID,
            amount=-amount,
            available_delta=_ZERO,
            withdrawal_id=withdrawal_id,
            remark=remark or "提现打款完成",
            frozen_delta=-amount,
            withdrawn_delta=amount,
        )

    async def admin_adjust(
        self,
        wallet: Wallet,
        *,
        amount: Decimal,
        operator_id: int,
        reason: str,
    ) -> WalletTransaction:
        """
        Admin manual adjustment. Positive adds to available balance,
        negative deducts from it. Zero and negative-resulting amounts
        are rejected.
        """
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount == _ZERO:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="调整金额不能为0",
            )
        if amount < _ZERO:
            new_balance = _to_decimal(wallet.available_balance) + amount
            if new_balance < _ZERO:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="调整后余额不能为负数",
                )
        return await self._apply(
            wallet,
            tx_type=WalletTransactionType.ADMIN_ADJUST,
            amount=amount,
            available_delta=amount,
            operator_id=operator_id,
            remark=reason,
        )

    # ------------------------------------------------------------------
    # Order settlement
    # ------------------------------------------------------------------

    async def settle_order_income(
        self,
        order: Order,
        payout_amount: Decimal | None = None,
        note: str | None = None,
        booster_id: int | None = None,
    ) -> Optional[WalletTransaction]:
        """
        Credit a booster's income for an order.

        Default income: order.price * (1 - COMMISSION_RATE), rounded to cents.
        payout_amount（部分到账）直接覆盖该金额，不再计佣金。
        note 为老板打款备注，写入流水 remark 留存。
        booster_id 指定结算给哪个打手（名额制下每个打手独立结算）；
        缺省沿用订单的首抢打手 order.booster_id。

        Idempotent per booster via the (order_id, booster_id,
        type='ORDER_INCOME') unique constraint: if the ledger row already
        exists the duplicate-key error is caught inside a savepoint and None
        is returned (already settled).
        """
        if booster_id is None:
            booster_id = order.booster_id
        if booster_id is None:
            return None

        # Fast path: already settled for this booster?
        existing = await self._db.execute(
            select(WalletTransaction.id).where(
                WalletTransaction.order_id == order.id,
                WalletTransaction.booster_id == booster_id,
                WalletTransaction.type == WalletTransactionType.ORDER_INCOME,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None

        if payout_amount is not None:
            income = Decimal(str(payout_amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
            remark = f"订单 #{order.id} 部分到账"
            if note:
                remark = f"{remark}：{note}"
        else:
            commission_rate = Decimal(str(settings.COMMISSION_RATE))
            income = (
                _to_decimal(order.price) * (Decimal("1") - commission_rate)
            ).quantize(_CENT, rounding=ROUND_HALF_UP)
            remark = f"订单 #{order.id} 结算收入"
            if note:
                remark = f"{remark}：{note}"

        if income < _ZERO:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="打款金额不能为负数",
            )
        if income == _ZERO:
            # 0 元打款：credit 拒收 0 金额，这里经 _apply 直记一条 0 元
            # ORDER_INCOME 流水（余额/累计收入不动），只占幂等位，
            # 保证重复审核走唯一键幂等返回，不重不漏。
            try:
                async with self._db.begin_nested():
                    wallet = await self.get_or_create_wallet(booster_id)
                    transaction = await self._apply(
                        wallet,
                        tx_type=WalletTransactionType.ORDER_INCOME,
                        amount=_ZERO,
                        available_delta=_ZERO,
                        order_id=order.id,
                        booster_id=booster_id,
                        remark=remark,
                    )
                    return transaction
            except IntegrityError:
                logger.info(
                    "Order %s income already settled for booster %s",
                    order.id,
                    booster_id,
                )
                return None

        try:
            async with self._db.begin_nested():
                wallet = await self.get_or_create_wallet(booster_id)
                transaction = await self.credit(
                    wallet,
                    amount=income,
                    tx_type=WalletTransactionType.ORDER_INCOME,
                    order_id=order.id,
                    booster_id=booster_id,
                    remark=remark,
                )
                return transaction
        except IntegrityError:
            # Concurrent settlement won the race - this booster's payout for
            # the order is already recorded.
            logger.info(
                "Order %s income already settled for booster %s",
                order.id,
                booster_id,
            )
            return None

    # ------------------------------------------------------------------
    # User-publishing escrow & compensation deposit
    # ------------------------------------------------------------------

    async def hold_escrow(
        self,
        wallet: Wallet,
        *,
        amount: Decimal,
        order_id: int,
        remark: str | None = None,
    ) -> WalletTransaction:
        """发单托管冻结（发布人）：可用余额扣减、冻结余额增加。

        非管理员发布订单时冻结 price × max_claims，一次性操作。
        """
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount <= _ZERO:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="托管金额必须大于0",
            )
        locked = await self._lock_wallet(wallet.id)
        if _to_decimal(locked.available_balance) < amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="可用余额不足以托管该订单",
            )
        return await self._apply(
            wallet,
            tx_type=WalletTransactionType.ESCROW_HOLD,
            amount=-amount,
            available_delta=-amount,
            order_id=order_id,
            remark=remark or f"订单 #{order_id} 发单托管冻结",
            frozen_delta=amount,
        )

    async def release_escrow(
        self,
        wallet: Wallet,
        *,
        amount: Decimal,
        order_id: int,
        remark: str | None = None,
    ) -> WalletTransaction | None:
        """托管解冻退回（发布人）：冻结余额扣减、可用余额回补。

        实际解冻金额受当前冻结余额约束（防超释），不足时按可解冻
        部分处理并记录 warning。
        """
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount <= _ZERO:
            return None
        locked = await self._lock_wallet(wallet.id)
        frozen = _to_decimal(locked.frozen_balance)
        actual = min(amount, frozen)
        if actual <= _ZERO:
            logger.warning(
                "Order %s escrow release skipped: publisher frozen balance is 0",
                order_id,
            )
            return None
        if actual < amount:
            logger.warning(
                "Order %s escrow release capped: requested %s but frozen %s",
                order_id,
                amount,
                frozen,
            )
        return await self._apply(
            wallet,
            tx_type=WalletTransactionType.ESCROW_RELEASE,
            amount=actual,
            available_delta=actual,
            order_id=order_id,
            remark=remark or f"订单 #{order_id} 托管解冻退回",
            frozen_delta=-actual,
        )

    async def pay_order_from_escrow(
        self,
        order: Order,
        *,
        booster_id: int,
        amount: Decimal,
        note: str | None = None,
    ) -> WalletTransaction | None:
        """订单打款（发布人侧）：从其托管冻结余额中支出该打手的结算金额。

        - 按 (order_id, booster_id, type=ORDER_PAYMENT) 幂等防重；
        - 冻结不足时按现有冻结余额尽力扣减并 log warning，不阻断打手
          入账（老板兜底）。
        """
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount <= _ZERO:
            return None

        existing = await self._db.execute(
            select(WalletTransaction.id).where(
                WalletTransaction.order_id == order.id,
                WalletTransaction.booster_id == booster_id,
                WalletTransaction.type == WalletTransactionType.ORDER_PAYMENT,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None

        wallet = await self.get_or_create_wallet(order.user_id)
        locked = await self._lock_wallet(wallet.id)
        frozen = _to_decimal(locked.frozen_balance)
        actual = min(amount, frozen)
        if actual <= _ZERO:
            logger.warning(
                "Order %s payout %s for booster %s not deducted from escrow: "
                "publisher frozen balance is 0 (boss bottom line)",
                order.id,
                amount,
                booster_id,
            )
            return None
        if actual < amount:
            logger.warning(
                "Order %s payout for booster %s capped: expected %s, frozen %s "
                "(boss bottom line)",
                order.id,
                booster_id,
                amount,
                frozen,
            )

        remark = f"订单 #{order.id} 打款给打手"
        if note:
            remark = f"{remark}：{note}"
        try:
            async with self._db.begin_nested():
                return await self._apply(
                    wallet,
                    tx_type=WalletTransactionType.ORDER_PAYMENT,
                    amount=-actual,
                    available_delta=_ZERO,
                    order_id=order.id,
                    booster_id=booster_id,
                    remark=remark,
                    frozen_delta=-actual,
                )
        except IntegrityError:
            logger.info(
                "Order %s payout already recorded for booster %s",
                order.id,
                booster_id,
            )
            return None

    async def hold_deposit(
        self,
        wallet: Wallet,
        *,
        amount: Decimal,
        order_id: int,
        booster_id: int,
        remark: str | None = None,
    ) -> WalletTransaction:
        """接单冻结炸单赔偿金（打手）：可用余额扣减、冻结余额增加。

        幂等由 (order_id, booster_id, type=DEPOSIT_HOLD) 唯一键保证。
        """
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount <= _ZERO:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="赔偿金额必须大于0",
            )
        locked = await self._lock_wallet(wallet.id)
        if _to_decimal(locked.available_balance) < amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="余额不足以冻结炸单赔偿金",
            )
        return await self._apply(
            wallet,
            tx_type=WalletTransactionType.DEPOSIT_HOLD,
            amount=-amount,
            available_delta=-amount,
            order_id=order_id,
            booster_id=booster_id,
            remark=remark or f"订单 #{order_id} 接单冻结炸单赔偿金",
            frozen_delta=amount,
        )

    async def release_deposit(
        self,
        wallet: Wallet,
        *,
        amount: Decimal,
        order_id: int,
        booster_id: int,
        note: str | None = None,
    ) -> WalletTransaction | None:
        """炸单赔偿金解冻返还（打手）：冻结余额扣减、可用余额回补。"""
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount <= _ZERO:
            return None
        locked = await self._lock_wallet(wallet.id)
        frozen = _to_decimal(locked.frozen_balance)
        actual = min(amount, frozen)
        if actual <= _ZERO:
            logger.warning(
                "Order %s deposit release for booster %s skipped: frozen is 0",
                order_id,
                booster_id,
            )
            return None
        if actual < amount:
            logger.warning(
                "Order %s deposit release for booster %s capped: requested %s, frozen %s",
                order_id,
                booster_id,
                amount,
                frozen,
            )
        remark = f"订单 #{order_id} 炸单赔偿金解冻返还"
        if note:
            remark = f"{remark}：{note}"
        return await self._apply(
            wallet,
            tx_type=WalletTransactionType.DEPOSIT_RELEASE,
            amount=actual,
            available_delta=actual,
            order_id=order_id,
            booster_id=booster_id,
            remark=remark,
            frozen_delta=-actual,
        )

    async def deduct_compensation(
        self,
        wallet: Wallet,
        *,
        amount: Decimal,
        order_id: int,
        booster_id: int,
        note: str | None = None,
    ) -> WalletTransaction | None:
        """炸单赔偿扣除（打手）：从冻结余额中扣除，不返还。"""
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount <= _ZERO:
            return None
        locked = await self._lock_wallet(wallet.id)
        frozen = _to_decimal(locked.frozen_balance)
        actual = min(amount, frozen)
        if actual <= _ZERO:
            logger.warning(
                "Order %s compensation deduction for booster %s skipped: frozen is 0",
                order_id,
                booster_id,
            )
            return None
        if actual < amount:
            logger.warning(
                "Order %s compensation deduction for booster %s capped: requested %s, frozen %s",
                order_id,
                booster_id,
                amount,
                frozen,
            )
        remark = f"订单 #{order_id} 炸单赔偿扣除"
        if note:
            remark = f"{remark}：{note}"
        return await self._apply(
            wallet,
            tx_type=WalletTransactionType.COMPENSATION_DEDUCT,
            amount=-actual,
            available_delta=_ZERO,
            order_id=order_id,
            booster_id=booster_id,
            remark=remark,
            frozen_delta=-actual,
        )

    # ------------------------------------------------------------------
    # Withdrawal requests
    # ------------------------------------------------------------------

    async def create_withdrawal(
        self,
        user: User,
        *,
        amount: Decimal,
        channel,
        account_name: str,
        account_no: str,
        qrcode_url: str | None = None,
    ) -> WithdrawalRequest:
        """
        Create a PENDING withdrawal and freeze the amount.

        If the freeze fails (insufficient balance) the HTTPException rolls
        back the whole transaction, including the new request row.
        qrcode_url is expected to be pre-validated by the endpoint (must
        live under the caller's own /uploads/withdrawals/{user_id}/ folder).
        """
        amount = _to_decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount < Decimal("1.00"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="提现金额不能低于1元",
            )

        wallet = await self.get_or_create_wallet(user.id)

        withdrawal = WithdrawalRequest(
            user_id=user.id,
            amount=amount,
            channel=channel,
            account_name=account_name,
            account_no=account_no,
            qrcode_url=qrcode_url,
            status=WithdrawalStatus.PENDING,
        )
        self._db.add(withdrawal)
        await self._db.flush()
        await self._db.refresh(withdrawal)

        await self.freeze(
            wallet,
            amount=amount,
            withdrawal_id=withdrawal.id,
            remark=f"提现申请 #{withdrawal.id}",
        )

        logger.info(
            "Withdrawal %s created by user %s, amount=%s",
            withdrawal.id,
            user.id,
            amount,
        )
        return withdrawal

    async def get_withdrawal(self, withdrawal_id: int) -> WithdrawalRequest:
        result = await self._db.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.id == withdrawal_id)
        )
        withdrawal = result.scalar_one_or_none()
        if withdrawal is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="提现记录不存在",
            )
        return withdrawal

    async def review_withdrawal(
        self,
        withdrawal_id: int,
        admin: User,
        *,
        approve: bool,
        reason: str | None = None,
    ) -> WithdrawalRequest:
        """
        Admin reviews a PENDING withdrawal.

        approve: status -> APPROVED (amount stays frozen).
        reject: status -> REJECTED, frozen amount unfrozen back to
        available balance with a WITHDRAWAL_REFUND ledger entry.
        """
        result = await self._db.execute(
            select(WithdrawalRequest)
            .where(WithdrawalRequest.id == withdrawal_id)
            .with_for_update()
        )
        withdrawal = result.scalar_one_or_none()
        if withdrawal is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="提现记录不存在",
            )

        if withdrawal.status != WithdrawalStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有待审核的提现才能审核",
            )

        if not approve and not reason:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="驳回提现必须填写原因",
            )

        withdrawal.reviewed_by = admin.id
        withdrawal.reviewed_at = datetime.now(timezone.utc)

        if approve:
            withdrawal.status = WithdrawalStatus.APPROVED
            logger.info("Withdrawal %s approved by admin %s", withdrawal.id, admin.id)
        else:
            withdrawal.status = WithdrawalStatus.REJECTED
            withdrawal.reject_reason = reason
            wallet = await self.get_or_create_wallet(withdrawal.user_id)
            await self.unfreeze(
                wallet,
                amount=_to_decimal(withdrawal.amount),
                withdrawal_id=withdrawal.id,
                remark=f"提现驳回 #{withdrawal.id}: {reason}",
            )
            logger.info("Withdrawal %s rejected by admin %s", withdrawal.id, admin.id)

        await self._db.flush()
        await self._db.refresh(withdrawal)
        return withdrawal

    async def mark_withdrawal_paid(
        self,
        withdrawal_id: int,
        admin: User,
        *,
        payment_reference: str,
    ) -> WithdrawalRequest:
        """
        Admin marks an APPROVED withdrawal as PAID: deducts the frozen
        amount, accumulates total_withdrawn and records the payout.
        """
        result = await self._db.execute(
            select(WithdrawalRequest)
            .where(WithdrawalRequest.id == withdrawal_id)
            .with_for_update()
        )
        withdrawal = result.scalar_one_or_none()
        if withdrawal is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="提现记录不存在",
            )

        if withdrawal.status != WithdrawalStatus.APPROVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有已审核通过的提现才能标记打款",
            )

        withdrawal.status = WithdrawalStatus.PAID
        withdrawal.payment_reference = payment_reference
        withdrawal.paid_by = admin.id
        withdrawal.paid_at = datetime.now(timezone.utc)

        wallet = await self.get_or_create_wallet(withdrawal.user_id)
        await self.settle_withdrawal(
            wallet,
            amount=_to_decimal(withdrawal.amount),
            withdrawal_id=withdrawal.id,
            remark=f"提现打款 #{withdrawal.id}",
        )

        await self._db.flush()
        await self._db.refresh(withdrawal)

        logger.info(
            "Withdrawal %s marked paid by admin %s, ref=%s",
            withdrawal.id,
            admin.id,
            payment_reference,
        )
        return withdrawal

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def list_transactions(
        self,
        user_id: int,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[WalletTransaction], int]:
        """Paginated ledger entries for a user's wallet, newest first."""
        wallet = await self.get_or_create_wallet(user_id)

        count_result = await self._db.execute(
            select(func.count(WalletTransaction.id)).where(
                WalletTransaction.wallet_id == wallet.id
            )
        )
        total = int(count_result.scalar() or 0)

        result = await self._db.execute(
            select(WalletTransaction)
            .where(WalletTransaction.wallet_id == wallet.id)
            .order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def list_withdrawals(
        self,
        *,
        user_id: int | None = None,
        status_filter: WithdrawalStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[WithdrawalRequest], int]:
        """Paginated withdrawal requests, newest first. user_id=None lists all."""
        query = select(WithdrawalRequest)
        count_query = select(func.count(WithdrawalRequest.id))

        if user_id is not None:
            query = query.where(WithdrawalRequest.user_id == user_id)
            count_query = count_query.where(WithdrawalRequest.user_id == user_id)
        if status_filter is not None:
            query = query.where(WithdrawalRequest.status == status_filter)
            count_query = count_query.where(WithdrawalRequest.status == status_filter)

        total = int((await self._db.execute(count_query)).scalar() or 0)

        result = await self._db.execute(
            query.order_by(WithdrawalRequest.created_at.desc(), WithdrawalRequest.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total


def get_wallet_service(db: AsyncSession) -> WalletService:
    """
    Factory function to create WalletService instance.

    Args:
        db: Async database session.

    Returns:
        WalletService instance.
    """
    return WalletService(db)
