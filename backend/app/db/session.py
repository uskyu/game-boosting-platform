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
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
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
