"""
Main FastAPI application module.
Entry point for the Game Boosting Platform API.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import api_router
from app.core.config import settings
from app.db.session import async_session_factory, close_db, init_db
from app.services.user_service import get_user_service

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
UPLOAD_PATH = Path(settings.UPLOAD_DIR)
UPLOAD_PATH.mkdir(parents=True, exist_ok=True)

# 到账时效自动结算后台任务（优雅关闭时 cancel）
PAYOUT_SCAN_INTERVAL_SECONDS = 600  # 每 10 分钟扫描一次
_payout_scan_task: asyncio.Task | None = None


async def _payout_scan_loop() -> None:
    """周期扫描到账时效到期的交付名额并自动结算。

    逐条 try/except：单次扫描失败只 log，等待下一轮；取消时立即退出。
    """
    from app.services.payout_scheduler import scan_due_payouts

    while True:
        await asyncio.sleep(PAYOUT_SCAN_INTERVAL_SECONDS)
        try:
            async with async_session_factory() as session:
                settled_ids = await scan_due_payouts(session)
                await session.commit()
            if settled_ids:
                logger.info(
                    "Payout delay scheduler settled %s claims", len(settled_ids)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Payout delay scheduler scan failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.
    Handles startup and shutdown events.
    """
    global _payout_scan_task
    # Startup
    logger.info("Starting Game Boosting Platform API...")
    try:
        await init_db()
        upload_dir = Path(settings.UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with async_session_factory() as session:
                user_service = get_user_service(session)
                await user_service.ensure_default_admin()
                await session.commit()
        except SQLAlchemyError:
            logger.warning(
                "Skip default admin bootstrap before migrations are applied."
            )

        logger.info("Database connection established")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        raise

    # 到账时效自动结算后台任务
    _payout_scan_task = asyncio.create_task(_payout_scan_loop())
    logger.info("Payout delay scheduler started (interval=%ss)", PAYOUT_SCAN_INTERVAL_SECONDS)

    yield

    # Shutdown
    logger.info("Shutting down Game Boosting Platform API...")
    if _payout_scan_task is not None:
        _payout_scan_task.cancel()
        try:
            await _payout_scan_task
        except asyncio.CancelledError:
            pass
        _payout_scan_task = None
        logger.info("Payout delay scheduler stopped")
    await close_db()
    logger.info("Database connection closed")


# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    description="游戏代练服务平台 API - Game Boosting Platform API",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    docs_url=f"{settings.API_V1_PREFIX}/docs",
    redoc_url=f"{settings.API_V1_PREFIX}/redoc",
    lifespan=lifespan,
)

# Configure CORS – only allow the methods and headers actually used by the
# frontend.  Avoid wildcards so the browser enforces a stricter policy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

logger.info("Configured CORS origins: %s", settings.cors_origins)

# Serve uploaded files in development/runtime container.
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_PATH)), name="uploads")


# 用户可读的中文表单字段名，覆盖常见请求字段；未映射的字段回退为原始英文名。
FIELD_LABELS_ZH = {
    "email": "邮箱",
    "username": "昵称",
    "password": "密码",
    "current_password": "当前密码",
    "new_password": "新密码",
    "captcha_id": "验证码",
    "captcha_code": "验证码",
    "title": "标题",
    "game": "游戏",
    "game_name": "游戏名称",
    "service_type": "服务类型",
    "price": "价格",
    "amount": "金额",
    "deadline": "截止时间",
    "note": "备注",
    "description": "描述",
    "content": "内容",
    "current_rank": "当前段位",
    "target_rank": "目标段位",
    "proof_url": "证明材料",
    "name": "名称",
    "days": "天数",
}


# Custom exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """
    Handle Pydantic validation errors with Chinese messages that include the
    concrete reason (field label + limit) so the frontend can show it directly.
    """
    translated_errors = []
    for error in exc.errors():
        loc_parts = [str(loc) for loc in error["loc"] if loc != "body"]
        field = ".".join(loc_parts)
        leaf = loc_parts[-1] if loc_parts else ""
        label = FIELD_LABELS_ZH.get(leaf, leaf)
        error_type = error["type"]
        ctx = error.get("ctx") or {}

        if error_type == "missing":
            msg = f"请填写{label}" if label else "存在未填写的必填项"
        elif error_type == "string_too_short":
            minimum = ctx.get("min_length")
            msg = f"{label}至少需要 {minimum} 个字符" if minimum else f"{label}长度不足"
        elif error_type == "string_too_long":
            maximum = ctx.get("max_length")
            msg = f"{label}最多 {maximum} 个字符" if maximum else f"{label}长度超限"
        elif error_type == "string_pattern_mismatch":
            msg = f"{label}格式不正确"
        elif error_type in ("greater_than", "greater_than_equal", "less_than", "less_than_equal"):
            msg = f"{label}数值超出允许范围"
        elif error_type == "value_error":
            custom = str(ctx.get("error") or "").strip()
            if leaf == "email":
                # 邮箱库的错误信息是英文，统一换成中文
                msg = "邮箱格式不正确"
            elif custom and any("\u4e00" <= ch <= "\u9fff" for ch in custom):
                # schema 自定义校验器抛出的中文错误，直接使用
                msg = custom
            else:
                msg = f"{label}格式不正确"
        elif error_type in ("enum", "literal_error"):
            msg = f"{label}取值不合法"
        elif error_type in ("int_parsing", "float_parsing", "int_from_float"):
            msg = f"{label}必须是数字"
        elif error_type == "json_invalid":
            msg = "请求内容不是有效的 JSON"
        else:
            msg = f"{label}填写有误（{error['msg']}）"

        translated_errors.append({"field": field, "message": msg})

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "请求参数验证失败",
            "errors": translated_errors,
        },
    )


# Include API router
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


# Health check endpoint
@app.get("/health", tags=["健康检查"])
async def health_check() -> dict:
    """
    Health check endpoint for container orchestration.
    """
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": "1.0.0",
    }


# Root endpoint
@app.get("/", tags=["根路径"])
async def root() -> dict:
    """
    Root endpoint with API information.
    """
    return {
        "message": "欢迎使用游戏代练服务平台 API",
        "docs": f"{settings.API_V1_PREFIX}/docs",
        "redoc": f"{settings.API_V1_PREFIX}/redoc",
    }
