"""
Main FastAPI application module.
Entry point for the Game Boosting Platform API.
"""

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.
    Handles startup and shutdown events.
    """
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

    yield

    # Shutdown
    logger.info("Shutting down Game Boosting Platform API...")
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


# Custom exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """
    Handle Pydantic validation errors with Chinese messages.
    """
    errors = exc.errors()

    # Translate common validation messages
    translated_errors = []
    for error in errors:
        field = ".".join(str(loc) for loc in error["loc"] if loc != "body")
        error_type = error["type"]

        # Map error types to Chinese messages
        message_map = {
            "missing": f"字段 '{field}' 不能为空",
            "string_too_short": f"字段 '{field}' 长度不足",
            "string_too_long": f"字段 '{field}' 长度超限",
            "value_error": f"字段 '{field}' 格式错误",
            "type_error": f"字段 '{field}' 类型错误",
            "json_invalid": "JSON格式无效",
        }

        msg = message_map.get(error_type, f"字段 '{field}' 验证失败: {error['msg']}")
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
