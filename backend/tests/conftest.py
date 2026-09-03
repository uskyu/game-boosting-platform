"""Shared test fixtures. Uses a dedicated test database in the same MySQL."""

import asyncio
import os
from typing import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_async_session
from app.main import app
from app.models.base import Base

# Build test DB URL: replace database name with test variant
_prod_url = settings.DB_URL
_test_url = _prod_url.rsplit("/", 1)[0] + "/game_boosting_test"
_parsed_url = make_url(_prod_url)
_app_db_user = _parsed_url.username
_admin_url = _parsed_url.set(
    username=os.getenv("MYSQL_ROOT_USER", "root"),
    password=os.getenv("MYSQL_ROOT_PASSWORD") or _parsed_url.password,
    database="mysql",
).render_as_string(hide_password=False)

_engine = create_async_engine(_test_url, echo=False, poolclass=NullPool)
_session_factory = async_sessionmaker(bind=_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop():
    """Use one event loop for the complete test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_database():
    """Create test database and all tables once per session."""
    # Create the test database if it doesn't exist.
    # Preferred path: a root/admin connection. On managed servers where root
    # is not reachable (or the password differs), fall back to the app user,
    # which has ALL PRIVILEGES on the already-provisioned game_boosting_test
    # database (CREATE DATABASE IF NOT EXISTS succeeds on an existing DB).
    admin_engine = create_async_engine(_admin_url, echo=False, poolclass=NullPool)
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text("CREATE DATABASE IF NOT EXISTS game_boosting_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
            if _app_db_user:
                await conn.execute(text(f"GRANT ALL PRIVILEGES ON game_boosting_test.* TO '{_app_db_user}'@'%'"))
                await conn.execute(text("FLUSH PRIVILEGES"))
    except OperationalError:
        pass  # root unreachable - rely on the app user's existing grants
    finally:
        await admin_engine.dispose()

    # Create all tables
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    # Drop all tables after session
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _engine.dispose()


@pytest.fixture(autouse=True)
async def reset_database() -> AsyncGenerator[None, None]:
    """Reset schema for each test to avoid data leakage between cases."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture(autouse=True)
def override_db_session() -> AsyncGenerator[None, None]:
    """Override app DB dependency with a fresh session per request."""

    async def _override_get_async_session() -> AsyncGenerator[AsyncSession, None]:
        async with _session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_async_session] = _override_get_async_session
    yield
    app.dependency_overrides.pop(get_async_session, None)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Direct database session for test-side mutations."""
    async with _session_factory() as session:
        yield session
        await session.close()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as ac:
        yield ac


@pytest.fixture
def make_captcha():
    """Captcha factory: call _make() for each registration (single-use)."""
    def _make() -> dict:
        from app.services import captcha_service
        captcha_id, _ = captcha_service.create()
        code, _ = captcha_service._store[captcha_id]
        return {"captcha_id": captcha_id, "captcha_code": code}
    return _make


@pytest.fixture
async def registered_user(client: AsyncClient, make_captcha) -> dict:
    """Register and return a regular user with tokens."""
    resp = await client.post("/auth/register", json={
        "email": "testuser@example.com",
        "username": "TestUser",
        "password": "TestPass123",
        **make_captcha(),
    })
    assert resp.status_code in (200, 201)
    return resp.json()


@pytest.fixture
async def booster_user(client: AsyncClient, db_session: AsyncSession, make_captcha) -> dict:
    """Register a user then promote to BOOSTER via DB."""
    resp = await client.post("/auth/register", json={
        "email": "booster@example.com",
        "username": "TestBooster",
        "password": "BoostPass123",
        **make_captcha(),
    })
    assert resp.status_code in (200, 201)
    data = resp.json()

    # Promote to booster
    from app.models.user import User, UserRole
    from sqlalchemy import select
    result = await db_session.execute(select(User).where(User.email == "booster@example.com"))
    user = result.scalar_one()
    user.role = UserRole.BOOSTER
    user.booster_quota = 5
    await db_session.commit()

    # Re-login to get token with updated role
    login_resp = await client.post("/auth/login", json={
        "email": "booster@example.com",
        "password": "BoostPass123",
    })
    assert login_resp.status_code == 200
    return login_resp.json()


@pytest.fixture
async def admin_user(client: AsyncClient, db_session: AsyncSession, make_captcha) -> dict:
    """Register a user then promote to ADMIN via DB."""
    resp = await client.post("/auth/register", json={
        "email": "admin_test@example.com",
        "username": "TestAdmin",
        "password": "AdminPass123",
        **make_captcha(),
    })
    assert resp.status_code in (200, 201)

    from app.models.user import User, UserRole
    from sqlalchemy import select
    result = await db_session.execute(select(User).where(User.email == "admin_test@example.com"))
    user = result.scalar_one()
    user.role = UserRole.ADMIN
    await db_session.commit()

    login_resp = await client.post("/auth/login", json={
        "email": "admin_test@example.com",
        "password": "AdminPass123",
    })
    assert login_resp.status_code == 200
    return login_resp.json()


def auth_header(user_data: dict) -> dict:
    """Build Authorization header from login/register response."""
    return {"Authorization": f"Bearer {user_data['access_token']}"}
