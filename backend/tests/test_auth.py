"""Authentication tests: register + login."""

from httpx import AsyncClient


async def test_register_success(client: AsyncClient):
    resp = await client.post("/auth/register", json={
        "email": "new@example.com",
        "username": "NewUser",
        "password": "StrongPass1",
    })
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["user"]["email"] == "new@example.com"
    assert "access_token" in data


async def test_register_duplicate_email(client: AsyncClient):
    payload = {
        "email": "dup@example.com",
        "username": "First",
        "password": "StrongPass1",
    }
    resp1 = await client.post("/auth/register", json=payload)
    assert resp1.status_code in (200, 201)

    payload["username"] = "Second"
    resp2 = await client.post("/auth/register", json=payload)
    assert resp2.status_code == 400


async def test_register_weak_password(client: AsyncClient):
    resp = await client.post("/auth/register", json={
        "email": "weak@example.com",
        "username": "WeakUser",
        "password": "nodigits",
    })
    assert resp.status_code == 422


async def test_login_success(client: AsyncClient):
    # Register first
    await client.post("/auth/register", json={
        "email": "login@example.com",
        "username": "LoginUser",
        "password": "LoginPass1",
    })

    resp = await client.post("/auth/login", json={
        "email": "login@example.com",
        "password": "LoginPass1",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


async def test_login_wrong_password(client: AsyncClient):
    await client.post("/auth/register", json={
        "email": "wrongpw@example.com",
        "username": "WrongPw",
        "password": "CorrectPass1",
    })

    resp = await client.post("/auth/login", json={
        "email": "wrongpw@example.com",
        "password": "WrongPassword1",
    })
    assert resp.status_code == 401
