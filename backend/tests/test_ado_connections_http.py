"""End-to-end HTTP tests for /api/ado-connections against real Postgres.

Overrides get_encryptor with a fixed test key (same pattern as test_cloud_connections_http.py)
and require_admin (covered separately by test_admin_gate_http.py).
"""

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.db.orm import AdoConnectionRow, Base, CloudConnectionRow, ProjectRow
from app.infrastructure.security.encryptor import Encryptor
from app.interface.http.deps import get_encryptor, get_session, require_admin
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)
_TEST_ENCRYPTION_KEY = "QMveDxMLB0eSF3PseIEr3fWyV7B0F5Ebk2KGC2JaZJk="


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
        await s.execute(delete(AdoConnectionRow))
        await s.execute(delete(CloudConnectionRow))
        await s.execute(delete(ProjectRow))
        await s.commit()
        s.add_all([ProjectRow(id=uuid.uuid4(), name="EVP"), ProjectRow(id=uuid.uuid4(), name="rxdevs")])
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_encryptor] = lambda: Encryptor(_TEST_ENCRYPTION_KEY)
    app.dependency_overrides[require_admin] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_create_and_list_connection(client):
    r = await client.post(
        "/api/ado-connections",
        json={"project": "EVP", "org": "my-org", "ado_project": "EVP-Board", "pat": "secret-pat"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["project"] == "EVP"
    assert body["org"] == "my-org"
    assert body["ado_project"] == "EVP-Board"
    assert body["work_item_type"] == "Bug"
    assert "pat" not in body

    r = await client.get("/api/ado-connections")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["project"] == "EVP"


async def test_create_without_pat_is_422(client):
    r = await client.post(
        "/api/ado-connections",
        json={"project": "EVP", "org": "my-org", "ado_project": "EVP-Board"},
    )
    assert r.status_code == 422


async def test_update_changes_ado_project_and_keeps_pat_when_omitted(client):
    r = await client.post(
        "/api/ado-connections",
        json={"project": "rxdevs", "org": "my-org", "ado_project": "old-board", "pat": "secret"},
    )
    connection_id = r.json()["id"]

    r = await client.patch(
        f"/api/ado-connections/{connection_id}",
        json={"project": "rxdevs", "org": "my-org", "ado_project": "new-board"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ado_project"] == "new-board"


async def test_create_unknown_project_is_422(client):
    r = await client.post(
        "/api/ado-connections",
        json={"project": "NOPE", "org": "my-org", "ado_project": "EVP-Board", "pat": "secret"},
    )
    assert r.status_code == 422


async def test_update_404_for_unknown_connection(client):
    r = await client.patch(
        "/api/ado-connections/00000000-0000-0000-0000-000000000000",
        json={"project": "EVP", "org": "my-org", "ado_project": "EVP-Board", "pat": "x"},
    )
    assert r.status_code == 404


async def test_delete_connection(client):
    r = await client.post(
        "/api/ado-connections",
        json={"project": "EVP", "org": "my-org", "ado_project": "EVP-Board", "pat": "secret"},
    )
    connection_id = r.json()["id"]

    r = await client.delete(f"/api/ado-connections/{connection_id}")
    assert r.status_code == 204

    r = await client.get("/api/ado-connections")
    assert r.json() == []


async def test_test_connection_endpoint_reports_ok(client, monkeypatch):
    async def fake_verify(self):
        return None

    monkeypatch.setattr(
        "app.infrastructure.tickets.ado_client.AdoTicketClient.verify", fake_verify
    )

    r = await client.post(
        "/api/ado-connections",
        json={"project": "EVP", "org": "my-org", "ado_project": "EVP-Board", "pat": "secret"},
    )
    connection_id = r.json()["id"]

    r = await client.post(f"/api/ado-connections/{connection_id}/test")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "error": None}


async def test_test_connection_endpoint_reports_the_failure_reason(client, monkeypatch):
    async def fake_verify(self):
        raise RuntimeError("HTTP 401")

    monkeypatch.setattr(
        "app.infrastructure.tickets.ado_client.AdoTicketClient.verify", fake_verify
    )

    r = await client.post(
        "/api/ado-connections",
        json={"project": "EVP", "org": "my-org", "ado_project": "EVP-Board", "pat": "bad-pat"},
    )
    connection_id = r.json()["id"]

    r = await client.post(f"/api/ado-connections/{connection_id}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "401" in body["error"]
