"""指定派单候选人接口。

路由必须注册在 ``orders_router`` 之前：本路径 ``/orders/designation-candidates``
会被 ``/orders/{order_id}``（int 路径参数）抢先匹配成 422。

- 登录用户可用，且必须实际可发单（全站开关/封禁口径与发单接口一致）；
- ``q`` 为空返回「最近指定」，非空搜索昵称/登录账号并返回候选；
- 搜索按用户独立限流，最近列表不消耗搜索额度。
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas.designation import DesignationCandidatesResponse
from app.services import captcha_service, designation_service

router = APIRouter(prefix="/orders", tags=["订单"])

DESIGNATION_SEARCH_RATE_LIMIT_PER_MINUTE = 20
DESIGNATION_SEARCH_RATE_KEY_PREFIX = "designation_search"


@router.get(
    "/designation-candidates",
    response_model=DesignationCandidatesResponse,
    summary="指定派单候选人",
    description=(
        "q 为空返回最近 90 天指定过的打手（最多 6 人）；q 非空按昵称/登录账号"
        "搜索（至少 2 个字符，最多 10 人）。响应不含登录邮箱；can_accept=false "
        "的活跃用户仍返回，由前端标记「已停接」。"
    ),
)
async def list_designation_candidates(
    db: DatabaseSession,
    current_user: CurrentUser,
    q: Annotated[
        str | None,
        Query(
            max_length=100,
            description="打手昵称或登录账号关键词（至少 2 个字符）",
        ),
    ] = None,
) -> DesignationCandidatesResponse:
    await designation_service.ensure_can_designate(db, current_user)

    keyword = (q or "").strip()
    if not keyword:
        # 最近列表不消耗搜索额度，前端会缓存并复用这份结果。
        return DesignationCandidatesResponse(
            query="",
            recent=await designation_service.list_recent_candidates(db, current_user),
            results=[],
        )

    if len(keyword) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请输入至少 2 个字符",
        )

    # 仅关键词搜索限流：每用户每分钟 20 次，防脚本枚举全站用户。
    if not captcha_service.check_rate_limit(
        f"{DESIGNATION_SEARCH_RATE_KEY_PREFIX}:user:{current_user.id}",
        DESIGNATION_SEARCH_RATE_LIMIT_PER_MINUTE,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "designation_search_rate_limited",
                "message": "查找过于频繁，请稍候再试",
                "retry_after": 60,
            },
        )

    return DesignationCandidatesResponse(
        query=keyword,
        recent=[],
        results=await designation_service.search_candidates(db, current_user, keyword),
    )
