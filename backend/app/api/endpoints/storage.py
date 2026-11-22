"""存储相关的公共接口（非管理员）。

当前只有预签名 URL 的重新签发：COS 私有读 + 7 天签名意味着浏览器缓存里的
旧 URL 终有一天会过期（403）。前端 ``SignedImage`` 在图片加载失败时调用这里
换一串新 URL 并重试一次，不需要用户手动刷新页面。

权限在**签发时**判定（见 ``url_signing.authorize_url``）：与其它业务接口一样
由服务端强制校验，绝不依赖前端隐藏。无权访问与对象不存在返回同一句话，
避免把本接口变成 COS 对象的探针。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DatabaseSession
from app.api.rate_limit import enforce_user_limit
from app.services.url_signing import resign_for_user

router = APIRouter(prefix="/storage", tags=["存储"])
logger = logging.getLogger(__name__)

# 重新签发限流：正常使用一个人一天也点不出几次（页面缓存 7 天，只有过期后
# 才会触发），120 次/分钟足够覆盖「一次打开大量图片同时 403」的批量重试，
# 又能挡住拿本接口批量刷签名的脚本。
_RESIGN_RATE = (120, 60.0)
_RESIGN_RATE_SCOPE = "storage_resign"

# 统一文案：不区分「无权访问」与「对象不存在」。
_FORBIDDEN_DETAIL = "无权访问该图片"


class ResignRequest(BaseModel):
    """重新签发的请求体。"""

    url: str = Field(
        min_length=1,
        max_length=1000,
        description="需要重新签名的 /uploads 相对路径",
    )


class ResignResponse(BaseModel):
    """重新签发结果：新的签名 URL（COS 模式）或原 URL（LOCAL 模式）。"""

    url: str = Field(description="可直接用于图片 src 的 URL")


@router.post(
    "/resign",
    response_model=ResignResponse,
    summary="重新签发图片 URL",
    description=(
        "浏览器缓存的预签名 URL 过期（403）时调用，换取一串新的 7 天签名 URL。"
        "仅允许有权查看该图片的登录用户；无权与不存在统一返回 403。"
    ),
)
async def resign_image_url(
    payload: ResignRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
) -> ResignResponse:
    """权限校验通过后重新签发图片 URL。"""
    enforce_user_limit(
        current_user.id,
        _RESIGN_RATE_SCOPE,
        _RESIGN_RATE[0],
        _RESIGN_RATE[1],
        code="storage_resign_rate_limited",
    )

    url = await resign_for_user(db, current_user, payload.url)
    if url is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_FORBIDDEN_DETAIL,
        )
    return ResignResponse(url=url)
