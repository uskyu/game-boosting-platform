"""后台「保证金管理」：总开关、冷却天数、阶梯整表维护与保证金划转流水。"""

from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.orm import aliased

from app.api.deps import DatabaseSession, get_current_admin
from app.core.product_time import PRODUCT_TIMEZONE, product_day_bounds, product_local_date
from app.models.deposit import DepositSetting, DepositTier
from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction, WalletTransactionType
from app.schemas.deposit import (
    AdminDepositLedgerItem,
    AdminDepositLedgerResponse,
    AdminDepositLedgerStats,
    DepositSettingsAdminResponse,
    DepositSettingsAdminUpdate,
    DepositTierAdminResponse,
)
from app.services import deposit_service

router = APIRouter(prefix="/admin/deposit", tags=["admin-deposit"])

# 只统计真正改变保证金余额的划转：用户/管理员「余额 ⇄ 保证金」。
# 炸单/取消/售后扣款是保证金被抵扣，不属于「转入转出」，留在各自业务记录里。
_LEDGER_TYPES = (
    WalletTransactionType.DEPOSIT_TRANSFER_IN,
    WalletTransactionType.DEPOSIT_TRANSFER_OUT,
)


def _money(value: object) -> Decimal:
    """把 SQL 聚合结果统一成两位小数的 Decimal（None/0 → 0.00）。"""
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _tier_response(tier: DepositTier) -> DepositTierAdminResponse:
    return DepositTierAdminResponse(
        id=tier.id,
        threshold=tier.threshold,
        wait_seconds=tier.wait_seconds,
        exempt_compensation=tier.exempt_compensation,
        settle_hours=tier.settle_hours,
        daily_accept_limit=tier.daily_accept_limit,
        enabled=tier.enabled,
        updated_at=tier.updated_at,
    )


async def _settings_response(db, setting: DepositSetting) -> DepositSettingsAdminResponse:
    tiers = await deposit_service.list_tiers(db, enabled_only=False)
    return DepositSettingsAdminResponse(
        enabled=setting.enabled,
        return_cooldown_days=setting.return_cooldown_days,
        default_compensation=setting.default_compensation,
        settlement_mode=setting.settlement_mode,
        global_booster_quota=setting.global_booster_quota,
        updated_at=setting.updated_at,
        tiers=[_tier_response(t) for t in tiers],
    )


@router.get(
    "/settings",
    response_model=DepositSettingsAdminResponse,
    summary="获取保证金管理配置",
    description="返回保证金模式总开关、转回冷却天数与完整阶梯列表。",
)
async def get_deposit_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> DepositSettingsAdminResponse:
    setting = await deposit_service.get_or_create_deposit_setting(db)
    return await _settings_response(db, setting)


@router.put(
    "/settings",
    response_model=DepositSettingsAdminResponse,
    summary="保存保证金管理配置",
    description="保存总开关、冷却天数，并以整表替换方式保存阶梯（门槛不可重复）。",
)
async def update_deposit_settings(
    payload: DepositSettingsAdminUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> DepositSettingsAdminResponse:
    thresholds = [Decimal(str(t.threshold)).quantize(Decimal("0.01")) for t in payload.tiers]
    if len(set(thresholds)) != len(thresholds):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="保证金阶梯的门槛不能重复",
        )

    # 这里**故意不做**「阶梯必须含 0 元档」的校验（2026-10-18 撤除）。
    #
    # 曾经的实现要求存在启用中的 0 元档，理由是 resolve_tier() 会用
    # `or tiers[0]` 把 0 保证金打手兜底成最低一档，从而继承其权益。但该前提
    # 与老板的实际配置相反，硬拦会直接卡死后台保存：
    #   - 老板有意删掉了 0 元档——原始默认 0 元档是 exempt_compensation=False，
    #     会让 0 保证金打手接单时必须冻结炸单赔付金（余额 0 即 400 接不了单）；
    #     删掉后 0 保证金打手兜底到 100 档、继承免冻结，才接得上单。
    #   - 老板 2026-10-18 亦明确表态「0 保证金识别到 100 保证金那个问题不大」。
    # 因此「0 保证金兜底到最低档」是**有意的承重行为，不要当成 bug 修**：
    # 移除 0 元档就等于允许 0 保证金打手接单，属产品决策而非数据缺陷。

    setting = await deposit_service.get_or_create_deposit_setting(db)
    setting.enabled = payload.enabled
    setting.return_cooldown_days = payload.return_cooldown_days
    setting.settlement_mode = payload.settlement_mode
    setting.global_booster_quota = payload.global_booster_quota
    setting.default_compensation = Decimal(str(payload.default_compensation)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    setting.updated_by = current_admin.id
    await db.flush()

    # 整表替换：阶梯数量少、且由管理员一次提交完整列表，比逐条 diff 更不易出错
    await db.execute(delete(DepositTier))
    await db.flush()
    for item in payload.tiers:
        db.add(
            DepositTier(
                threshold=Decimal(str(item.threshold)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                wait_seconds=item.wait_seconds,
                exempt_compensation=item.exempt_compensation,
                # None = 该档不承诺加速（按订单到账时效），原样落库
                settle_hours=item.settle_hours,
                # 0 与 None 同义 = 不限，统一归一成 None 再写库
                daily_accept_limit=deposit_service.normalize_daily_accept_limit(
                    item.daily_accept_limit
                ),
                enabled=item.enabled,
            )
        )
    await db.flush()
    await db.refresh(setting)
    return await _settings_response(db, setting)


@router.get(
    "/transactions",
    response_model=AdminDepositLedgerResponse,
    summary="保证金划转流水",
    description=(
        "后台查看余额转入保证金 / 保证金转回余额的流水（含管理员手动调整）。"
        "默认查询北京时间今天；可按日期区间、方向、用户筛选，并返回区间汇总。"
    ),
)
async def list_deposit_transactions(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    start_date: Annotated[
        date | None,
        Query(description="起始日期（北京时间，含当天）"),
    ] = None,
    end_date: Annotated[
        date | None,
        Query(description="结束日期（北京时间，含当天）"),
    ] = None,
    direction: Annotated[
        Literal["all", "in", "out"],
        Query(description="方向：all=全部，in=转入保证金，out=转回余额"),
    ] = "all",
    q: Annotated[
        str | None,
        Query(max_length=64, description="按用户名 / 邮箱 / 用户ID筛选"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
) -> AdminDepositLedgerResponse:
    """保证金划转流水：列表 + 区间汇总。

    - 默认 start=end=北京时间今天；给 start 不给 end 时 end=今天；
      只给 end 时查当天。
    - 汇总不随 direction 变化：不管当前列表筛的是转入还是转回，
      顶部都能同时看到区间转入/转回总额与人数。
    - 查询最多 366 天，避免一次拉全量历史把库拖慢。
    """
    today = product_local_date(datetime.now(timezone.utc))
    start_day = start_date or end_date or today
    end_day = end_date or today
    if end_day < start_day:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="结束日期不能早于开始日期",
        )
    if (end_day - start_day).days > 366:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="查询范围不能超过 366 天",
        )

    # 北京时间自然日边界 → naive UTC，直接与库里的 created_at 比较。
    start_utc, _ = product_day_bounds(
        datetime.combine(start_day, time.min, tzinfo=PRODUCT_TIMEZONE)
    )
    _, end_utc = product_day_bounds(
        datetime.combine(end_day, time.min, tzinfo=PRODUCT_TIMEZONE)
    )

    conditions = [
        WalletTransaction.type.in_(_LEDGER_TYPES),
        WalletTransaction.created_at >= start_utc,
        WalletTransaction.created_at < end_utc,
    ]
    query_text = (q or "").strip()
    if query_text:
        if query_text.isdigit() and int(query_text) > 0:
            conditions.append(
                or_(
                    User.id == int(query_text),
                    User.username.contains(query_text),
                    User.email.contains(query_text),
                )
            )
        else:
            conditions.append(
                or_(
                    User.username.contains(query_text),
                    User.email.contains(query_text),
                )
            )

    list_conditions = list(conditions)
    if direction == "in":
        list_conditions.append(
            WalletTransaction.type == WalletTransactionType.DEPOSIT_TRANSFER_IN
        )
    elif direction == "out":
        list_conditions.append(
            WalletTransaction.type == WalletTransactionType.DEPOSIT_TRANSFER_OUT
        )

    total = int(
        (
            await db.execute(
                select(func.count(WalletTransaction.id))
                .join(Wallet, Wallet.id == WalletTransaction.wallet_id)
                .join(User, User.id == Wallet.user_id)
                .where(*list_conditions)
            )
        ).scalar()
        or 0
    )

    operator = aliased(User)
    rows = (
        await db.execute(
            select(
                WalletTransaction.id.label("tx_id"),
                WalletTransaction.type.label("tx_type"),
                WalletTransaction.amount.label("tx_amount"),
                WalletTransaction.balance_before.label("available_before"),
                WalletTransaction.balance_after.label("available_after"),
                WalletTransaction.remark.label("remark"),
                WalletTransaction.order_id.label("order_id"),
                WalletTransaction.operator_id.label("operator_id"),
                WalletTransaction.created_at.label("created_at"),
                Wallet.user_id.label("user_id"),
                User.username.label("username"),
                User.email.label("email"),
                Wallet.deposit_balance.label("current_deposit_balance"),
                operator.username.label("operator_username"),
            )
            .join(Wallet, Wallet.id == WalletTransaction.wallet_id)
            .join(User, User.id == Wallet.user_id)
            .outerjoin(operator, operator.id == WalletTransaction.operator_id)
            .where(*list_conditions)
            .order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items: list[AdminDepositLedgerItem] = []
    for row in rows:
        is_in = row.tx_type == WalletTransactionType.DEPOSIT_TRANSFER_IN
        deposit_amount = _money(row.tx_amount)
        # 可用余额变动直接取流水快照差：用户自助划转时等于 amount；
        # 管理员 adjust_deposit 只动保证金池，快照差自然是 0，不能拿 amount 当余额变动。
        available_change = _money(row.available_after) - _money(row.available_before)
        deposit_change = abs(deposit_amount) if is_in else -abs(deposit_amount)
        items.append(
            AdminDepositLedgerItem(
                id=row.tx_id,
                user_id=row.user_id,
                username=row.username,
                email=row.email,
                type=row.tx_type,
                direction="in" if is_in else "out",
                available_change=available_change,
                deposit_change=deposit_change,
                available_before=_money(row.available_before),
                available_after=_money(row.available_after),
                current_deposit_balance=_money(row.current_deposit_balance),
                remark=row.remark,
                order_id=row.order_id,
                operator_id=row.operator_id,
                operator_username=row.operator_username,
                created_at=row.created_at,
            )
        )

    stats_row = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                WalletTransaction.type
                                == WalletTransactionType.DEPOSIT_TRANSFER_IN,
                                func.abs(WalletTransaction.amount),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("in_amount"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                WalletTransaction.type
                                == WalletTransactionType.DEPOSIT_TRANSFER_IN,
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("in_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                WalletTransaction.type
                                == WalletTransactionType.DEPOSIT_TRANSFER_OUT,
                                func.abs(WalletTransaction.amount),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("out_amount"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                WalletTransaction.type
                                == WalletTransactionType.DEPOSIT_TRANSFER_OUT,
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("out_count"),
                func.count(
                    func.distinct(
                        case(
                            (
                                WalletTransaction.type
                                == WalletTransactionType.DEPOSIT_TRANSFER_OUT,
                                Wallet.user_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("out_users"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    WalletTransaction.type
                                    == WalletTransactionType.DEPOSIT_TRANSFER_OUT,
                                    WalletTransaction.operator_id.is_(None),
                                ),
                                func.abs(WalletTransaction.amount),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("self_out_amount"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    WalletTransaction.type
                                    == WalletTransactionType.DEPOSIT_TRANSFER_OUT,
                                    WalletTransaction.operator_id.is_(None),
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("self_out_count"),
                func.count(
                    func.distinct(
                        case(
                            (
                                and_(
                                    WalletTransaction.type
                                    == WalletTransactionType.DEPOSIT_TRANSFER_OUT,
                                    WalletTransaction.operator_id.is_(None),
                                ),
                                Wallet.user_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("self_out_users"),
            )
            .join(Wallet, Wallet.id == WalletTransaction.wallet_id)
            .join(User, User.id == Wallet.user_id)
            .where(*conditions)
        )
    ).one()

    in_amount = _money(stats_row.in_amount)
    out_amount = _money(stats_row.out_amount)
    self_out_amount = _money(stats_row.self_out_amount)
    stats = AdminDepositLedgerStats(
        in_amount=in_amount,
        in_count=int(stats_row.in_count or 0),
        out_amount=out_amount,
        out_count=int(stats_row.out_count or 0),
        out_users=int(stats_row.out_users or 0),
        self_out_amount=self_out_amount,
        self_out_count=int(stats_row.self_out_count or 0),
        self_out_users=int(stats_row.self_out_users or 0),
        net_amount=in_amount - out_amount,
    )

    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return AdminDepositLedgerResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        start_date=start_day,
        end_date=end_day,
        stats=stats,
    )
