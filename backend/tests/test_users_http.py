"""End-to-end HTTP tests for /api/users (admin-only account management) against real Postgres.

Overrides get_current_user directly (see test_projects_http.py's docstring for why this is the
right dependency to override rather than any specific require_role(...) call) to exercise both the
"admin can do everything" path and the "sre/consultant get 403" gate.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.users.entities import User
from app.infrastructure.db.orm import Base, UserRow
from app.interface.http.deps import get_current_user, get_session
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)

_ADMIN_USER = User(
    id=uuid.uuid4(),
    username="admin",
    email="admin@test.local",
    password_hash="unused",
    role="admin",
    created_at=datetime.now(timezone.utc),
)
_SRE_USER = User(
    id=uuid.uuid4(),
    username="sre",
    email="sre@test.local",
    password_hash="unused",
    role="sre",
    created_at=datetime.now(timezone.utc),
)
_CONSULTANT_USER = User(
    id=uuid.uuid4(),
    username="consultant",
    email="consultant@test.local",
    password_hash="unused",
    role="consultant",
    created_at=datetime.now(timezone.utc),
)


@pytest.fixture()
async def client():
    engine = create_async_engine(_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"Postgres not reachable for HTTP test: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(UserRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: _ADMIN_USER

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_admin_can_create_and_list_users(client):
    r = await client.post(
        "/api/users", json={"username": "new.sre", "password": "password123", "role": "sre"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == "new.sre"
    assert body["role"] == "sre"
    assert "password" not in body
    assert "password_hash" not in body

    r = await client.get("/api/users")
    assert r.status_code == 200
    assert [u["username"] for u in r.json()] == ["new.sre"]


async def test_create_with_unknown_role_is_422(client):
    r = await client.post(
        "/api/users", json={"username": "x", "password": "password123", "role": "wizard"}
    )
    assert r.status_code == 422


async def test_create_duplicate_username_is_409(client):
    await client.post(
        "/api/users", json={"username": "dup", "password": "password123", "role": "sre"}
    )
    r = await client.post(
        "/api/users", json={"username": "dup", "password": "password123", "role": "sre"}
    )
    assert r.status_code == 409


async def test_admin_can_update_a_users_role(client):
    r = await client.post(
        "/api/users",
        json={"username": "promote", "password": "password123", "role": "consultant"},
    )
    user_id = r.json()["id"]

    r = await client.patch(f"/api/users/{user_id}", json={"role": "sre"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "sre"


async def test_update_unknown_user_is_404(client):
    r = await client.patch(
        "/api/users/00000000-0000-0000-0000-000000000000", json={"role": "sre"}
    )
    assert r.status_code == 404


async def test_admin_can_delete_a_user(client):
    r = await client.post(
        "/api/users",
        json={"username": "delete-me", "password": "password123", "role": "consultant"},
    )
    user_id = r.json()["id"]

    r = await client.delete(f"/api/users/{user_id}")
    assert r.status_code == 204

    r = await client.get("/api/users")
    assert r.json() == []


async def test_admin_can_reset_a_users_password(client):
    r = await client.post(
        "/api/users",
        json={"username": "reset-me", "password": "password123", "role": "consultant"},
    )
    user_id = r.json()["id"]

    r = await client.post(f"/api/users/{user_id}/password", json={"new_password": "newpassword456"})
    assert r.status_code == 200, r.text
    assert "password" not in r.json()
    assert "password_hash" not in r.json()

    r = await client.post("/api/auth/login", json={"username": "reset-me", "password": "newpassword456"})
    assert r.status_code == 200, r.text


async def test_reset_password_too_short_is_422(client):
    r = await client.post(
        "/api/users",
        json={"username": "short-pw", "password": "password123", "role": "consultant"},
    )
    user_id = r.json()["id"]

    r = await client.post(f"/api/users/{user_id}/password", json={"new_password": "short"})
    assert r.status_code == 422


async def test_reset_password_for_unknown_user_is_404(client):
    r = await client.post(
        "/api/users/00000000-0000-0000-0000-000000000000/password",
        json={"new_password": "newpassword456"},
    )
    assert r.status_code == 404


async def test_delete_unknown_user_is_404(client):
    r = await client.delete("/api/users/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_sre_gets_403_on_user_management(client):
    app.dependency_overrides[get_current_user] = lambda: _SRE_USER
    r = await client.get("/api/users")
    assert r.status_code == 403


async def test_consultant_gets_403_on_user_management(client):
    app.dependency_overrides[get_current_user] = lambda: _CONSULTANT_USER
    r = await client.get("/api/users")
    assert r.status_code == 403
