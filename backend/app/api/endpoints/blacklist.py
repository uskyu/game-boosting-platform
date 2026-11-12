"""发单人黑名单接口。

只允许 ADMIN 或当前实际可发单的登录用户使用（受全站发单权限开关约束）；
不提供任何「列出用户 / 模糊搜索用户」能力，lookup 仅做实名/邮箱完全匹配。
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas.blacklist import (
    PublisherBlacklistCreate,
    PublisherBlacklistItem,
    PublisherBlacklistListResponse,
    PublisherBlacklistLookupResponse,
)
from app.services import blacklist_service, captcha_service

router = APIRouter(prefix="/blacklist", tags=["黑名单"])

# lookup 防枚举：每用户每分钟 10 次（独立键空间，不与 captcha 场景桶混用）
LOOKUP_RATE_LIMIT_PER_MINUTE = 10
LOOKUP_RATE_KEY_PREFIX = "blacklist_lookup"


@router.get(
    "/mine",
    response_model=PublisherBlacklistListResponse,
    summary="我的黑名单",
    description="只返回当前用户自己拉黑的人；q 只对已拉黑列表做用户名过滤",
)
async def list_my_blacklist(
    db: DatabaseSession,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1, le=100000, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
    q: Annotated[
        str | None,
        Query(max_length=100, description="按已拉黑用户名过滤（不搜索全站）"),
    ] = None,
) -> PublisherBlacklistListResponse:
    await blacklist_service.ensure_can_manage_blacklist(db, current_user)
    items, total = await blacklist_service.list_mine(
        db,
        current_user,
        q=q,
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return PublisherBlacklistListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/lookup",
    response_model=PublisherBlacklistLookupResponse,
    summary="精确查找一个用户",
    description=(
        "只接受用户名或登录邮箱的完全匹配，最多返回 1 人；"
        "不支持用户ID枚举、前缀/模糊搜索，也不返回候选列表。"
    ),
)
async def lookup_blacklist_user(
    db: DatabaseSession,
    current_user: CurrentUser,
    q: Annotated[
        str,
        Query(
            min_length=2,
            max_length=255,
            description="用户名或登录邮箱（必须完全一致）",
        ),
    ],
) -> PublisherBlacklistLookupResponse:
    await blacklist_service.ensure_can_manage_blacklist(db, current_user)

    keyword = q.strip()
    if len(keyword) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请输入至少 2 个字符",
        )

    # 每用户独立桶，防枚举；不共享 IP 桶（同 NAT 下会误伤正常用户）
    if not captcha_service.check_rate_limit(
        f"{LOOKUP_RATE_KEY_PREFIX}:user:{current_user.id}",
        LOOKUP_RATE_LIMIT_PER_MINUTE,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "blacklist_lookup_rate_limited",
                "message": "查找过于频繁，请稍候再试",
                "retry_after": 60,
            },
        )

    return await blacklist_service.lookup_exact(db, current_user, q=keyword)


@router.post(
    "",
    response_model=PublisherBlacklistItem,
    summary="拉黑用户",
    description="重复拉黑幂等返回已有记录；单发单人最多 500 条",
)
async def add_user_to_blacklist(
    payload: PublisherBlacklistCreate,
    db: DatabaseSession,
    current_user: CurrentUser,
) -> PublisherBlacklistItem:
    await blacklist_service.ensure_can_manage_blacklist(db, current_user)
    return await blacklist_service.add_blacklist(
        db,
        current_user,
        target_user_id=payload.user_id,
        reason=payload.reason,
    )


@router.delete(
    "/{blocked_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="解除拉黑",
    description="解除拉黑；未拉黑也返回 204（幂等）",
)
async def remove_user_from_blacklist(
    blocked_user_id: int,
    db: DatabaseSession,
    current_user: CurrentUser,
) -> Response:
    await blacklist_service.ensure_can_manage_blacklist(db, current_user)
    await blacklist_service.remove_blacklist(
        db,
        current_user,
        blocked_user_id=blocked_user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
