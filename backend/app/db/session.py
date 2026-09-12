"""
Database session management module.
Provides async engine and session factory for MySQL database operations.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.core.config import settings
from app.core.post_commit import drain_registry, start_registry, stop_registry

# Create async engine with connection pooling optimized for MySQL
engine: AsyncEngine = create_async_engine(
    settings.DB_URL,
    echo=settings.DEBUG,
    poolclass=AsyncAdaptedQueuePool,
    pool_size=20,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,  # Recycle connections every 30 minutes
    # aiomysql's async adapter does not accept SQLAlchemy's pre-ping argument.
    pool_pre_ping=False,
)

# Create async session factory
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency injection function for FastAPI.
    Yields an async database session and ensures proper cleanup.

    Usage:
        @router.get("/users")
        async def get_users(db: AsyncSession = Depends(get_async_session)):
            ...
    """
    async with async_session_factory() as session:
        token = start_registry()
        try:
            yield session
            await session.commit()
            # 事务已提交：此刻执行登记在案的推送/广播，行锁与连接已释放，
            # 慢客户端再也拖不住业务事务。钩子异常在 drain 内部吞掉记日志。
            await drain_registry()
        except Exception:
            await session.rollback()
            raise
        finally:
            stop_registry(token)
            await session.close()


async def init_db() -> None:
    """
    Initialize database connection.
    Called during application startup to verify connectivity.
    """
    async with engine.begin() as conn:
        # Test connection
        await conn.run_sync(lambda _: None)


async def close_db() -> None:
    """
    Close database connections.
    Called during application shutdown for graceful cleanup.
    """
    await engine.dispose()
