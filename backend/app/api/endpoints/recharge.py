"""充值（易支付）用户侧接口与异步回调。

- ``/wallet/recharge/config`` 读取充值入口配置（不含商户密钥）
- ``/wallet/recharge``        创建充值订单并返回支付表单参数
- ``/wallet/recharge/mine``   我的充值记录
- ``/wallet/recharge/notify`` 易支付回调（**公开无鉴权**，返回纯文本 success/fail）
"""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas.recharge import (
    PayMethodOption,
    RechargeConfigResponse,
    RechargeCreateRequest,
    RechargeCreateResponse,
    RechargeListResponse,
    RechargeResponse,
)
from app.services import recharge_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wallet/recharge", tags=["充值"])


@router.get(
    "/config",
    response_model=RechargeConfigResponse,
    summary="获取充值配置",
    description="返回充值是否开启、可用支付方式与最低充值金额；不包含任何商户密钥。",
)
async def get_recharge_config(
    current_user: CurrentUser,
    db: DatabaseSession,
) -> RechargeConfigResponse:
    setting = await recharge_service.get_or_create_payment_setting(db)
    enabled = recharge_service.is_recharge_enabled(setting)
    methods = recharge_service.enabled_pay_methods(setting) if enabled else []
    return RechargeConfigResponse(
        enabled=enabled,
        pay_methods=[PayMethodOption(**m) for m in methods],
        min_amount=setting.min_amount,
    )


@router.post(
    "",
    response_model=RechargeCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="发起充值",
    description="创建充值订单并返回易支付表单参数（前端以 POST 表单提交到 pay_url）。",
)
async def create_recharge(
    payload: RechargeCreateRequest,
    request: Request,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> RechargeCreateResponse:
    # 管理员同样可以充值（便于联调与测试），不做角色限制
    order, pay_url, params = await recharge_service.create_recharge_order(
        db,
        user=current_user,
        amount=payload.amount,
        payment_method=payload.payment_method,
        request=request,
    )
    return RechargeCreateResponse(
        trade_no=order.trade_no,
        amount=order.amount,
        pay_url=pay_url,
        params=params,
    )


@router.get(
    "/mine",
    response_model=RechargeListResponse,
    summary="我的充值记录",
    description="分页获取当前登录用户的充值记录（时间倒序）",
)
async def list_my_recharges(
    current_user: CurrentUser,
    db: DatabaseSession,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
) -> RechargeListResponse:
    orders, total = await recharge_service.list_user_recharges(
        db,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
    )
    return RechargeListResponse(
        items=[RechargeResponse.model_validate(order) for order in orders],
        total=total,
        page=page,
        page_size=page_size,
        pages=recharge_service._pages(total, page_size),
    )


@router.api_route(
    "/notify",
    methods=["GET", "POST"],
    summary="易支付回调",
    description="易支付异步通知入口。公开访问，校验签名后幂等入账，返回纯文本 success/fail。",
    include_in_schema=False,
)
async def recharge_notify(
    request: Request,
    db: DatabaseSession,
) -> PlainTextResponse:
    if request.method == "POST":
        try:
            form = await request.form()
        except Exception:
            logger.warning("Recharge notify: failed to parse POST form")
            return PlainTextResponse("fail")
        params = {key: str(value) for key, value in form.items()}
    else:
        params = {key: str(value) for key, value in request.query_params.items()}

    if not params:
        logger.warning("Recharge notify: empty params")
        return PlainTextResponse("fail")

    try:
        ok = await recharge_service.handle_notify(db, params=params)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Recharge notify processing failed")
        return PlainTextResponse("fail")

    return PlainTextResponse("success" if ok else "fail")
