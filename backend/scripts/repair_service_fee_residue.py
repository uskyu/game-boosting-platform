"""修复历史「服务费残留」：把已完结订单的托管冻结差额结转平台收入。

背景：修复前的结单逻辑只从发布人托管里扣打手净额，服务费差额
（``price - payout``）永久滞留在 ``wallets.frozen_balance``。本脚本逐单
检查 ``status='COMPLETED'`` 且发布人仍持有本单托管的订单，把剩余持有额
以 ``SERVICE_FEE_SETTLE`` 从冻结余额结转，并写入 ``platform_income``。

安全约束：
- 默认 dry-run，只打印计划，不写库；带 ``--apply`` 才落库；
- 只处理 COMPLETED 订单，绝不触碰 LOCKED/DELIVERED 等未完成订单；
- 每单独立事务，失败只回滚该单，不影响其他订单；
- 幂等：已存在服务费流水/平台收入的名额不会重复扣减；
- 用 ``--order-id`` 可只处理指定订单（便于验证）。

用法（在 backend 目录执行）::

    python -m scripts.repair_service_fee_residue
    python -m scripts.repair_service_fee_residue --apply
    python -m scripts.repair_service_fee_residue --apply --order-id 123
"""

import argparse
import asyncio
import logging
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.core.money import resolve_service_fee_rate
from app.db.session import async_session_factory
from app.models.order import ClaimLifecycleStatus, Order, OrderClaim, OrderStatus
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from app.services.order_service import get_order_service
from app.services.platform_income_service import record_order_service_fee
from app.services.wallet_service import get_wallet_service

logger = logging.getLogger("repair_service_fee_residue")

_ZERO = Decimal("0.00")
_CENT = Decimal("0.01")
_REPAIR_NOTE = "历史服务费残留修复"


def _to_decimal(value) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def _claim_remaining(db, order: Order, booster_id: int) -> Decimal:
    """该打手名额还剩多少托管未结转（price - 已打款 - 已结转服务费）。

    一名额一份托管：发单按 ``price`` 冻一个名额，结单时 ORDER_PAYMENT
    打净额、SERVICE_FEE_SETTLE 结转服务费。两者都扣过的名额 remaining=0；
    只扣过净额的历史数据 remaining 正好等于该名额的服务费。
    """
    paid_result = await db.execute(
        select(WalletTransaction.amount).where(
            WalletTransaction.order_id == order.id,
            WalletTransaction.booster_id == booster_id,
            WalletTransaction.type == WalletTransactionType.ORDER_PAYMENT,
        )
    )
    fee_result = await db.execute(
        select(WalletTransaction.amount).where(
            WalletTransaction.order_id == order.id,
            WalletTransaction.booster_id == booster_id,
            WalletTransaction.type == WalletTransactionType.SERVICE_FEE_SETTLE,
        )
    )
    paid = sum((abs(Decimal(str(v or 0))) for v in paid_result.scalars().all()), _ZERO)
    settled_fee = sum(
        (abs(Decimal(str(v or 0))) for v in fee_result.scalars().all()), _ZERO
    )
    remaining = _to_decimal(order.price) - paid - settled_fee
    return remaining if remaining > _ZERO else _ZERO


async def _candidate_booster_ids(db, order: Order) -> list[int]:
    """候选归属打手：优先 SETTLED 名额，其次订单首抢打手。"""
    candidates: list[int] = []
    result = await db.execute(
        select(OrderClaim.booster_id).where(
            OrderClaim.order_id == order.id,
            OrderClaim.status == ClaimLifecycleStatus.SETTLED,
        )
    )
    for booster_id in result.scalars().all():
        if booster_id is not None and booster_id not in candidates:
            candidates.append(booster_id)
    if order.booster_id is not None and order.booster_id not in candidates:
        candidates.append(order.booster_id)
    return candidates


async def _plan_pairs(db, order: Order) -> list[dict]:
    """按名额计算每笔待结转金额；已修复/无残留的名额自动排除。"""
    plans: list[dict] = []
    for booster_id in await _candidate_booster_ids(db, order):
        remaining = await _claim_remaining(db, order, booster_id)
        if remaining <= _ZERO:
            continue
        plans.append({"booster_id": booster_id, "amount": remaining})
    return plans


async def _wallet_frozen(db, order: Order) -> Decimal:
    """发布人当前冻结余额（只读；钱包不存在视为 0）。"""
    result = await db.execute(
        select(Wallet.frozen_balance).where(Wallet.user_id == order.user_id)
    )
    frozen = result.scalar_one_or_none()
    return Decimal(str(frozen)) if frozen is not None else _ZERO


async def _load_order(db, order_id: int, *, lock: bool) -> Order | None:
    statement = select(Order).where(Order.id == order_id)
    if lock:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def _plan_order(db, order_id: int) -> dict | None:
    """只读计算单笔修复计划；不满足条件时返回 None。"""
    order = await _load_order(db, order_id, lock=False)
    if order is None:
        logger.warning("订单 #%s 不存在，跳过", order_id)
        return None
    if order.status != OrderStatus.COMPLETED:
        logger.info("订单 #%s 状态为 %s，不是已完成，跳过", order_id, order.status.value)
        return None

    order_service = get_order_service(db)
    if not await order_service._publisher_requires_escrow(order):
        logger.info("订单 #%s 是管理员发布（无托管），跳过", order_id)
        return None

    held = await order_service._publisher_escrow_held(order)
    if held <= _ZERO:
        logger.info("订单 #%s 无服务费残留（held=0），跳过", order_id)
        return None

    pairs = await _plan_pairs(db, order)
    if not pairs:
        logger.warning(
            "订单 #%s 仍有 %s 托管残留，但没有任何名额可归属（可能已部分修复），"
            "请人工核查",
            order_id,
            held,
        )
        return None

    frozen = await _wallet_frozen(db, order)
    planned = min(sum((p["amount"] for p in pairs), _ZERO), held, frozen)
    rate = (
        resolve_service_fee_rate(order.service_fee_rate) * Decimal("100")
    ).quantize(_CENT, rounding=ROUND_HALF_UP)
    return {
        "order_id": order.id,
        "publisher_id": order.user_id,
        "held": held,
        "frozen": frozen,
        "planned_fee": planned if planned > _ZERO else _ZERO,
        "pairs": pairs,
        "rate": rate,
    }


async def _repair_order(order_id: int) -> Decimal:
    """在一个独立事务里修复单笔订单（可含多个名额）；返回实际结转金额。"""
    async with async_session_factory() as db:
        order = await _load_order(db, order_id, lock=True)
        if order is None or order.status != OrderStatus.COMPLETED:
            logger.info("订单 #%s 不存在或不是已完成，跳过", order_id)
            return _ZERO

        order_service = get_order_service(db)
        if not await order_service._publisher_requires_escrow(order):
            return _ZERO

        pairs = await _plan_pairs(db, order)
        if not pairs:
            return _ZERO

        wallet_service = get_wallet_service(db)
        rate = (
            resolve_service_fee_rate(order.service_fee_rate) * Decimal("100")
        ).quantize(_CENT, rounding=ROUND_HALF_UP)
        total = _ZERO
        for pair in pairs:
            booster_id = pair["booster_id"]
            # 每笔都按当前实际持有额封顶，避免把别的订单/名额的钱带走
            held_now = await order_service._publisher_escrow_held(order)
            amount = min(pair["amount"], max(held_now, _ZERO))
            if amount <= _ZERO:
                continue
            fee_tx = await wallet_service.settle_service_fee(
                order,
                booster_id=booster_id,
                amount=amount,
                note=_REPAIR_NOTE,
            )
            if fee_tx is None:
                continue
            actual = abs(Decimal(str(fee_tx.amount)))
            income = await record_order_service_fee(
                db,
                order_id=order.id,
                booster_id=booster_id,
                amount=actual,
                rate=rate,
            )
            if income is None:
                # 收入写不进去就不能扣钱：整单回滚，留待人工核查。
                logger.warning(
                    "订单 #%s 打手 #%s 平台收入写入失败（可能已存在），"
                    "已回滚本次扣减",
                    order_id,
                    booster_id,
                )
                await db.rollback()
                return _ZERO
            total += actual
            logger.info(
                "[APPLY] 订单 #%s 打手 #%s 结转服务费 ¥%s（费率 %s%%）",
                order.id,
                booster_id,
                actual,
                rate,
            )

        if total <= _ZERO:
            await db.rollback()
            return _ZERO
        await db.commit()
        logger.info(
            "[APPLY] 订单 #%s 发布人 #%s 合计结转 ¥%s",
            order.id,
            order.user_id,
            total,
        )
        return total


async def _collect_order_ids(db, order_id: int | None) -> list[int]:
    statement = select(Order.id)
    if order_id is not None:
        # 指定单号时不做状态过滤：由 _plan_order/_repair_order 打印
        # 「不是已完成」并安全跳过，方便单笔验证时看到原因。
        statement = statement.where(Order.id == order_id)
    else:
        statement = statement.where(Order.status == OrderStatus.COMPLETED)
    statement = statement.order_by(Order.id)
    result = await db.execute(statement)
    return [int(value) for value in result.scalars().all()]


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="修复已完成订单残留的服务费冻结（默认 dry-run）"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写库；缺省只打印计划（dry-run）",
    )
    parser.add_argument(
        "--order-id",
        type=int,
        default=None,
        help="只处理指定订单ID（便于单笔验证）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    async with async_session_factory() as db:
        order_ids = await _collect_order_ids(db, args.order_id)

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info("模式=%s，待检查已完成订单 %s 笔", mode, len(order_ids))

    planned_total = _ZERO
    repaired_total = _ZERO
    repaired_count = 0
    skipped_count = 0

    for oid in order_ids:
        if not args.apply:
            async with async_session_factory() as db:
                plan = await _plan_order(db, oid)
            if plan is None:
                skipped_count += 1
                continue
            planned_total += plan["planned_fee"]
            logger.info(
                "[DRY-RUN] 订单 #%s 发布人 #%s：本单持有 ¥%s，冻结 ¥%s，"
                "计划结转 ¥%s，名额明细 %s（费率 %s%%）",
                plan["order_id"],
                plan["publisher_id"],
                plan["held"],
                plan["frozen"],
                plan["planned_fee"],
                [
                    {"booster_id": p["booster_id"], "amount": str(p["amount"])}
                    for p in plan["pairs"]
                ],
                plan["rate"],
            )
            continue

        try:
            actual = await _repair_order(oid)
        except Exception:  # noqa: BLE001 - 单笔失败不影响其他订单
            logger.exception("订单 #%s 修复失败，已跳过", oid)
            skipped_count += 1
            continue
        if actual > _ZERO:
            repaired_total += actual
            repaired_count += 1
        else:
            skipped_count += 1

    if args.apply:
        logger.info(
            "完成：修复 %s 笔，跳过 %s 笔，结转服务费合计 ¥%s",
            repaired_count,
            skipped_count,
            repaired_total,
        )
    else:
        logger.info(
            "dry-run 完成：计划修复 %s 笔，跳过 %s 笔，预计结转 ¥%s；"
            "确认无误后加 --apply 执行",
            len(order_ids) - skipped_count,
            skipped_count,
            planned_total,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
