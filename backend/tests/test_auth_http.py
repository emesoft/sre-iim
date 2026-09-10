"""End-to-end HTTP tests for POST /api/auth/login and GET /api/auth/me against real Postgres.

Overrides get_settings so this suite doesn't depend on the ambient .env for JWT_SECRET_KEY, same
pattern the old test_admin_gate_http.py used for ADMIN_JWT_SECRET (this file replaces it now that
login is per-user instead of a single shared admin password).
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.config import Settings, get_settings
from app.infrastructure.db.orm import Base, UserRow
from app.infrastructure.security.passwords import hash_password
from app.interface.http.deps import get_session
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)
_TEST_SETTINGS = Settings(jwt_secret_key="test-jwt-secret")
_PASSWORD = "correct-horse-battery"


@pytest.fixture()
async def client():
    app.dependency_overrides[get_settings] = lambda: _TEST_SETTINGS

    engine = create_async_engine(_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        app.dependency_overrides.clear()
        pytest.skip(f"Postgres not reachable for HTTP test: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(UserRow))
        s.add(
            UserRow(
                username="sre",
                email="sre@test.local",
                password_hash=hash_password(_PASSWORD),
                role="sre",
            )
        )
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_login_with_correct_password_returns_a_token_and_user(client):
    r = await client.post(
        "/api/auth/login", json={"username": "sre", "password": _PASSWORD}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body
    assert body["user"]["username"] == "sre"
    assert body["user"]["role"] == "sre"
    assert "password_hash" not in body["user"]


async def test_login_with_wrong_password_is_401(client):
    r = await client.post("/api/auth/login", json={"username": "sre", "password": "nope"})
    assert r.status_code == 401


async def test_login_with_unknown_username_is_401(client):
    r = await client.post(
        "/api/auth/login", json={"username": "nobody", "password": _PASSWORD}
    )
    assert r.status_code == 401


async def test_me_without_a_token_is_401(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_me_with_a_bogus_token_is_401(client):
    r = await client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


async def test_me_with_a_valid_token_returns_the_logged_in_user(client):
    login = await client.post(
        "/api/auth/login", json={"username": "sre", "password": _PASSWORD}
    )
    token = login.json()["token"]
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "sre"
    assert r.json()["role"] == "sre"


async def test_login_with_unset_jwt_secret_is_503_not_401(client):
    # A misconfigured server (JWT_SECRET_KEY unset) must read as "server broken", not "wrong
    # password" — same fail-closed convention the old admin gate used.
    app.dependency_overrides[get_settings] = lambda: Settings(jwt_secret_key="")
    r = await client.get("/api/auth/me", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 503
