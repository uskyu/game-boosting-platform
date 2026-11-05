"""管理员支付配置（网关管理）+ 支付记录接口。

商户密钥只以 ``has_key`` 暴露「是否已配置」，永远不会原样下发前端。
回调地址不需要配置：按充值请求来源自动推导。
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import DatabaseSession, get_current_admin
from app.models.payment_gateway import PaymentGateway
from app.models.payment_setting import PaymentSetting
from app.models.recharge import RechargeOrder
from app.models.user import User
from app.schemas.recharge import (
    AdminRechargeListResponse,
    AdminRechargeResponse,
    AdminRechargeStatusUpdate,
    AdminRechargeSyncResponse,
    AdminRechargeUser,
    PaymentGatewayCreate,
    PaymentGatewayResponse,
    PaymentGatewayUpdate,
    PaymentSettingResponse,
    PaymentSettingUpdate,
)
from app.services import payment_gateway_service, recharge_query_service, recharge_service

router = APIRouter(prefix="/admin/payment", tags=["admin-payment"])


def _gateway_response(gateway: PaymentGateway) -> PaymentGatewayResponse:
    """网关配置的后台视图：密钥只回 has_key，地址回显「实际提交到」。"""
    try:
        resolved = payment_gateway_service.resolve_submit_url(gateway)
    except payment_gateway_service.GatewayAddressError:
        # 历史脏数据不该让整个配置页 500：原样回显，让管理员改掉它
        resolved = gateway.submit_url

    return PaymentGatewayResponse(
        id=gateway.id,
        name=gateway.name,
        submit_url=gateway.submit_url,
        append_submit_php=gateway.append_submit_php,
        merchant_id=gateway.merchant_id,
        has_key=bool(gateway.secret_key),
        query_url=gateway.query_url,
        alipay_enabled=gateway.alipay_enabled,
        wxpay_enabled=gateway.wxpay_enabled,
        enabled=gateway.enabled,
        is_active=gateway.is_active,
        resolved_submit_url=resolved,
        supports_query=payment_gateway_service.resolve_query_url(gateway) is not None,
    )


def _settings_response(
    setting: PaymentSetting, gateways: list[PaymentGateway]
) -> PaymentSettingResponse:
    """全局设置 + 网关列表；顶层旧字段镜像「当前启用」的那一家。"""
    active = next((item for item in gateways if item.is_active), None)
    return PaymentSettingResponse(
        enabled=setting.enabled,
        min_amount=setting.min_amount,
        active_gateway_id=active.id if active else None,
        gateways=[_gateway_response(item) for item in gateways],
        updated_at=setting.updated_at,
        # 旧版前端字段（下一版前端会只读 gateways）
        pay_address=active.submit_url if active else None,
        epay_id=active.merchant_id if active else None,
        has_key=bool(active and active.secret_key),
        alipay_enabled=active.alipay_enabled if active else False,
        wxpay_enabled=active.wxpay_enabled if active else False,
    )


@router.get(
    "/settings",
    response_model=PaymentSettingResponse,
    summary="获取支付设置",
    description=(
        "返回充值总开关、最低金额与全部支付网关。"
        "每家网关的密钥仅以 has_key 表示是否已配置。"
    ),
)
async def get_payment_settings(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PaymentSettingResponse:
    setting = await recharge_service.get_or_create_payment_setting(db)
    gateways = await payment_gateway_service.list_gateways(db)
    return _settings_response(setting, gateways)


@router.put(
    "/settings",
    response_model=PaymentSettingResponse,
    summary="保存支付设置",
    description=(
        "保存充值总开关与最低充值金额。"
        "旧版前端的 pay_address / epay_id / epay_key 等字段仍然接受，"
        "有值时写入当前启用的网关，避免滚动发布期间把配置清空。"
    ),
)
async def update_payment_settings(
    payload: PaymentSettingUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PaymentSettingResponse:
    setting = await recharge_service.get_or_create_payment_setting(db)
    setting.enabled = payload.enabled
    setting.min_amount = Decimal(str(payload.min_amount)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # 旧版前端兼容：把旧字段落到当前启用的网关上
    legacy_touched = (
        any(
            value is not None
            for value in (payload.pay_address, payload.epay_id, payload.epay_key)
        )
        or payload.alipay_enabled is not None
        or payload.wxpay_enabled is not None
    )
    if legacy_touched:
        gateways = await payment_gateway_service.list_gateways(db)
        active = next((item for item in gateways if item.is_active), None)
        if active is None and gateways:
            # 有网关但都不是「当前使用」：旧前端没有这个概念，落到第一条上
            active = gateways[0]
        if active is None:
            # 库里一条网关都没有（老库刚升级、或测试环境）：
            # 旧版前端只会给这一套凭据，就地补一条并设为当前使用。
            if payload.pay_address and payload.epay_id and payload.epay_key:
                active = await payment_gateway_service.create_gateway(
                    db,
                    name="默认易支付",
                    submit_url=payload.pay_address,
                    append_submit_php=True,
                    merchant_id=payload.epay_id,
                    secret_key=payload.epay_key,
                    query_url=None,
                    alipay_enabled=payload.alipay_enabled is not False,
                    wxpay_enabled=payload.wxpay_enabled is not False,
                    enabled=True,
                )
        else:
            if payload.pay_address:
                # 旧字段同样要过新规则：只填域名必须当场报错，
                # 否则会静默打到根路径下另一个商户入口
                try:
                    payment_gateway_service.normalize_submit_url(
                        payload.pay_address, append_submit_php=active.append_submit_php
                    )
                except payment_gateway_service.GatewayAddressError as err:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)
                    ) from err
                active.submit_url = payload.pay_address
            if payload.epay_id:
                active.merchant_id = payload.epay_id
            if payload.epay_key:
                active.secret_key = payload.epay_key
            if payload.alipay_enabled is not None:
                active.alipay_enabled = payload.alipay_enabled
            if payload.wxpay_enabled is not None:
                active.wxpay_enabled = payload.wxpay_enabled

    setting.updated_by = current_admin.id
    await db.flush()
    await db.refresh(setting)

    gateways = await payment_gateway_service.list_gateways(db)
    return _settings_response(setting, gateways)


# ---------------------------------------------------------------------------
# 支付网关：增删改 + 设为当前使用
# ---------------------------------------------------------------------------


@router.get(
    "/gateways",
    response_model=list[PaymentGatewayResponse],
    summary="支付网关列表",
)
async def list_payment_gateways(
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> list[PaymentGatewayResponse]:
    gateways = await payment_gateway_service.list_gateways(db)
    return [_gateway_response(item) for item in gateways]


@router.post(
    "/gateways",
    response_model=PaymentGatewayResponse,
    status_code=status.HTTP_201_CREATED,
    summary="新增支付网关",
    description=(
        "新建一家支付网关。勾选「自动补 /submit.php」表示提交地址填的是基地址；"
        "不勾选表示填的是完整提交入口。库里还没有任何网关时，新建的会自动成为当前使用。"
    ),
)
async def create_payment_gateway(
    payload: PaymentGatewayCreate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PaymentGatewayResponse:
    gateway = await payment_gateway_service.create_gateway(
        db,
        name=payload.name or "",
        submit_url=payload.submit_url or "",
        append_submit_php=payload.append_submit_php,
        merchant_id=payload.merchant_id or "",
        secret_key=payload.secret_key or "",
        query_url=payload.query_url,
        alipay_enabled=payload.alipay_enabled,
        wxpay_enabled=payload.wxpay_enabled,
        enabled=payload.enabled,
    )
    return _gateway_response(gateway)


@router.put(
    "/gateways/{gateway_id}",
    response_model=PaymentGatewayResponse,
    summary="修改支付网关",
    description="修改网关配置；secret_key 留空表示保持原密钥不变。",
)
async def update_payment_gateway(
    gateway_id: int,
    payload: PaymentGatewayUpdate,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PaymentGatewayResponse:
    gateway = await payment_gateway_service.update_gateway(
        db,
        gateway_id,
        name=payload.name or "",
        submit_url=payload.submit_url or "",
        append_submit_php=payload.append_submit_php,
        merchant_id=payload.merchant_id or "",
        secret_key=payload.secret_key,
        query_url=payload.query_url,
        alipay_enabled=payload.alipay_enabled,
        wxpay_enabled=payload.wxpay_enabled,
        enabled=payload.enabled,
    )
    return _gateway_response(gateway)


@router.post(
    "/gateways/{gateway_id}/activate",
    response_model=PaymentGatewayResponse,
    summary="设为当前使用的网关",
    description="切换后只影响之后新发起的充值；已创建的订单仍按原网关验签与补单。",
)
async def activate_payment_gateway(
    gateway_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> PaymentGatewayResponse:
    gateway = await payment_gateway_service.activate_gateway(db, gateway_id)
    return _gateway_response(gateway)


@router.delete(
    "/gateways/{gateway_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除支付网关",
    description="当前使用中或已有充值记录引用的网关不能删除，只能停用。",
)
async def delete_payment_gateway(
    gateway_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> Response:
    await payment_gateway_service.delete_gateway(db, gateway_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# 支付记录：平台全量充值单 + 人工改判成功/失败 + 主动同步
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
        gateway_id=order.gateway_id,
        gateway_name=order.gateway_name,
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


@router.post(
    "/recharges/{recharge_id}/sync",
    response_model=AdminRechargeSyncResponse,
    summary="同步支付记录的上游状态",
    description=(
        "按商户单号主动查询上游订单，已支付且金额一致时补单入账。"
        "未支付不会改状态，金额不一致只报告不处理。"
        "该网关没有查询地址时返回 UNSUPPORTED。"
    ),
)
async def sync_recharge_record(
    recharge_id: int,
    db: DatabaseSession,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AdminRechargeSyncResponse:
    order = await db.get(RechargeOrder, recharge_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="支付记录不存在"
        )

    result = await recharge_query_service.sync_recharge_order(db, order)
    await db.flush()
    await db.refresh(order)

    return AdminRechargeSyncResponse(
        order=_recharge_response(order),
        result=result.outcome.value,
        message=result.message,
    )
