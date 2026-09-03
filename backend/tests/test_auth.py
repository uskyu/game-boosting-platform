"""Authentication tests: register + login."""

from httpx import AsyncClient


async def test_register_success(client: AsyncClient, captcha_pair: dict):
    resp = await client.post("/auth/register", json={
        "email": "new@example.com",
        "username": "NewUser",
        "password": "StrongPass1",
        **captcha_pair,
    })
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["user"]["email"] == "new@example.com"
    assert "access_token" in data


async def test_register_duplicate_email(client: AsyncClient, captcha_pair: dict):
    from app.services import captcha_service
    payload = {
        "email": "dup@example.com",
        "username": "First",
        "password": "StrongPass1",
        **captcha_pair,
    }
    resp1 = await client.post("/auth/register", json=payload)
    assert resp1.status_code in (200, 201)

    # captcha is single-use: generate a fresh pair for the second attempt
    captcha_id, _ = captcha_service.create()
    code, _ = captcha_service._store[captcha_id]
    payload["username"] = "Second"
    payload["captcha_id"] = captcha_id
    payload["captcha_code"] = code
    resp2 = await client.post("/auth/register", json=payload)
    assert resp2.status_code == 400


async def test_register_missing_captcha_rejected(client: AsyncClient):
    """Register without captcha fields -> 422 (pydantic required fields)."""
    resp = await client.post("/auth/register", json={
        "email": "nocaptcha@example.com",
        "username": "NoCaptcha",
        "password": "StrongPass1",
    })
    assert resp.status_code == 422


async def test_register_wrong_captcha_rejected(client: AsyncClient, captcha_pair: dict):
    """Register with wrong captcha code -> 400."""
    payload = {
        "email": "badcaptcha@example.com",
        "username": "BadCaptcha",
        "password": "StrongPass1",
        **captcha_pair,
    }
    payload["captcha_code"] = "WRONG"
    resp = await client.post("/auth/register", json=payload)
    assert resp.status_code == 400


async def test_register_weak_password(client: AsyncClient, captcha_pair: dict):
    resp = await client.post("/auth/register", json={
        "email": "weak@example.com",
        "username": "WeakUser",
        "password": "nodigits",
        **captcha_pair,
    })
    assert resp.status_code == 422


async def test_login_success(client: AsyncClient, captcha_pair: dict):
    # Register first
    await client.post("/auth/register", json={
        "email": "login@example.com",
        "username": "LoginUser",
        "password": "LoginPass1",
        **captcha_pair,
    })

    resp = await client.post("/auth/login", json={
        "email": "login@example.com",
        "password": "LoginPass1",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


async def test_login_wrong_password(client: AsyncClient, captcha_pair: dict):
    await client.post("/auth/register", json={
        "email": "wrongpw@example.com",
        "username": "WrongPw",
        "password": "CorrectPass1",
        **captcha_pair,
    })

    resp = await client.post("/auth/login", json={
        "email": "wrongpw@example.com",
        "password": "WrongPassword1",
    })
    assert resp.status_code == 401
