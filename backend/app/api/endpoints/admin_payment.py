"""管理员易支付配置 + 支付记录接口。

商户密钥只以 ``has_key`` 暴露「是否已配置」，永远不会原样下发前端。
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DatabaseSession, get_current_admin
from app.models.payment_setting import PaymentSetting
from app.models.recharge import RechargeOrder
from app.models.user import User
from app.schemas.recharge import (
    AdminRechargeListResponse,
    AdminRechargeResponse,
    AdminRechargeStatusUpdate,
    AdminRechargeUser,
    PaymentSettingResponse,
    PaymentSettingUpdate,
)
from app.services import recharge_service

router = APIRouter(prefix="/admin/payment", tags=["admin-payment"])


def _to_response(setting: PaymentSetting) -> PaymentSettingResponse:
    return PaymentSettingResponse(
        enabled=setting.enabled,
        pay_address=setting.pay_address,
        epay_id=setting.epay_id,
        has_key=bool(setting.epay_key),
        alipay_enabled=setting.alipay_enabled,
        wxpay_enabled=setting.wxpay_enabled,
        min_amount=setting.min_amount,
        updated_at=setting.updated_at,
    )


@router.get(
    "/settings",
    response_model=PaymentSettingResponse,
    summary="获取支付配置",
    description="读取易支付配置；商户密钥仅以 has_key 表示是否已配置。",
)
async def get_payment_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PaymentSettingResponse:
    setting = await recharge_service.get_or_create_payment_setting(db)
    return _to_response(setting)


@router.put(
    "/settings",
    response_model=PaymentSettingResponse,
    summary="保存支付配置",
    description="保存易支付接口地址、商户ID、商户密钥、启用的支付方式与最低充值金额。epay_key 留空表示不修改。回调地址无需配置，按充值请求来源自动推导。",
)
async def update_payment_settings(
    payload: PaymentSettingUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PaymentSettingResponse:
    setting = await recharge_service.get_or_create_payment_setting(db)

    setting.enabled = payload.enabled
    setting.pay_address = payload.pay_address
    setting.epay_id = payload.epay_id
    setting.alipay_enabled = payload.alipay_enabled
    setting.wxpay_enabled = payload.wxpay_enabled
    setting.min_amount = Decimal(str(payload.min_amount)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # 密钥留空 = 保持原值；只有明确传了新值才覆盖
    if payload.epay_key is not None:
        setting.epay_key = payload.epay_key

    setting.updated_by = current_admin.id
    await db.flush()
    await db.refresh(setting)
    return _to_response(setting)


# ---------------------------------------------------------------------------
# 支付记录：平台全量充值单 + 人工改判成功/失败
# ---------------------------------------------------------------------------


def _recharge_response(order: RechargeOrder) -> AdminRechargeResponse:
    """把充值单转成后台视图。

    ``user`` / ``marker`` 关系是 ``lazy="joined"``，随列表查询一次带出，
    不会因为逐行访问用户名而变成 N+1。
    """
    user = order.user
    marker = order.marker
    return AdminRechargeResponse(
        id=order.id,
        trade_no=order.trade_no,
        amount=order.amount,
        payment_method=order.payment_method,
        status=order.status,
        user=(
            AdminRechargeUser(id=user.id, username=user.username, email=user.email)
            if user is not None
            else None
        ),
        created_at=order.created_at,
        paid_at=order.paid_at,
        epay_trade_no=order.epay_trade_no,
        notify_payload=order.notify_payload,
        marked_by=(
            AdminRechargeUser(
                id=marker.id, username=marker.username, email=marker.email
            )
            if marker is not None
            else None
        ),
        marked_at=order.marked_at,
        admin_note=order.admin_note,
    )


@router.get(
    "/recharges",
    response_model=AdminRechargeListResponse,
    summary="支付记录列表",
    description=(
        "分页获取平台全部用户的充值（支付）记录，可按状态筛选、按单号/用户搜索。"
        "顶部统计 stats 始终是全量口径，不随筛选变化。"
    ),
)
async def list_recharge_records(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="按状态筛选：PENDING / SUCCESS / FAILED / CLOSED；留空为全部",
        ),
    ] = None,
    q: Annotated[
        str | None,
        Query(max_length=100, description="搜索商户单号 / 上游单号 / 用户名 / 邮箱"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminRechargeListResponse:
    orders, total, stats = await recharge_service.list_all_recharges(
        db,
        status_filter=status_filter,
        query=q,
        page=page,
        page_size=page_size,
    )
    return AdminRechargeListResponse(
        items=[_recharge_response(order) for order in orders],
        total=total,
        page=page,
        page_size=page_size,
        pages=recharge_service._pages(total, page_size),
        stats=stats,
    )


@router.post(
    "/recharges/{recharge_id}/status",
    response_model=AdminRechargeResponse,
    summary="改判支付记录状态",
    description=(
        "把一条支付记录改为成功或失败。金额由服务端按状态机判定："
        "改为成功 → 入账；改为失败 → 已入账的冲正扣回（未入账的不动钱）。"
        "改为失败必须填写原因，操作人与时间会记录在案。"
    ),
)
async def update_recharge_status(
    recharge_id: int,
    payload: AdminRechargeStatusUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AdminRechargeResponse:
    order, delta = await recharge_service.admin_update_status(
        db,
        recharge_id=recharge_id,
        target_status=payload.status,
        operator=current_admin,
        reason=payload.reason,
    )

    # 改判成功/失败后通知用户（内部吞异常：余额已经动了，通知失败不能回滚）
    await recharge_service.notify_recharge_updated(
        db, order=order, delta=delta, reason=payload.reason
    )

    return _recharge_response(order)
