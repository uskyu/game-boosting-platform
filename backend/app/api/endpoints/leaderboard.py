"""打手排行榜端点。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas.leaderboard import LeaderboardResponse
from app.services.leaderboard_service import get_leaderboard_service

router = APIRouter(prefix="/leaderboard", tags=["排行榜"])


@router.get(
    "",
    response_model=LeaderboardResponse,
    summary="打手排行榜",
    description=(
        "按周期内已接单金额排序，最多返回前 30 名；"
        "接手即计入、取消后扣除，并返回当前用户自己的名次与距上一名差额。"
    ),
)
async def get_leaderboard(
    current_user: CurrentUser,
    db: DatabaseSession,
    period: Annotated[
        Literal["day", "week", "all"],
        Query(description="榜单周期：day=日榜 / week=周榜 / all=总榜"),
    ] = "day",
) -> LeaderboardResponse:
    service = get_leaderboard_service(db)
    return await service.get_leaderboard(current_user, period=period)
