"""End-to-end HTTP tests for /api/cloud-connections against real Postgres.

Overrides get_alarm_fetcher with a fake so no real AWS call happens (same pattern as
test_documents_http.py overriding get_embedder). Alarm-created incidents no longer auto-analyze
(status="new" — see PollAlarmsJob's module docstring), so the poll test needs no analyzer/embedder
fakes at all.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cloud_connections.entities import AlarmState
from app.domain.users.entities import User
from app.infrastructure.db.orm import (
    AdoConnectionRow,
    AnalysisCacheRow,
    AnalysisRow,
    Base,
    CloudConnectionRow,
    IncidentRow,
    ProjectRow,
    TrackedAlarmRow,
)
from app.infrastructure.security.encryptor import Encryptor
from app.interface.http.deps import get_alarm_fetcher, get_current_user, get_encryptor, get_session
from app.main import app

_ADMIN_USER = User(
    id=uuid.uuid4(),
    username="admin",
    email="admin@test.local",
    password_hash="unused",
    role="admin",
    created_at=datetime.now(timezone.utc),
)

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)

# A valid Fernet key so this test suite passes regardless of the ambient SECRET_ENCRYPTION_KEY env
# var (get_settings() is @lru_cache'd module-level and not easily overridden per-test, so we
# override get_encryptor itself instead).
_TEST_ENCRYPTION_KEY = "QMveDxMLB0eSF3PseIEr3fWyV7B0F5Ebk2KGC2JaZJk="


class _FakeFetcher:
    def __init__(self, alarms=None, should_fail=False):
        self._alarms = alarms or []
        self._should_fail = should_fail

    async def list_alarms(self, connection):
        if self._should_fail:
            raise RuntimeError("access denied")
        return self._alarms


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
        await s.execute(delete(TrackedAlarmRow))
        await s.execute(delete(CloudConnectionRow))
        await s.execute(delete(AdoConnectionRow))
        await s.execute(delete(ProjectRow))
        await s.commit()
        s.add_all([ProjectRow(id=uuid.uuid4(), name="GCM"), ProjectRow(id=uuid.uuid4(), name="EVP")])
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_alarm_fetcher] = lambda: _FakeFetcher()
    app.dependency_overrides[get_encryptor] = lambda: Encryptor(_TEST_ENCRYPTION_KEY)
    app.dependency_overrides[get_current_user] = lambda: _ADMIN_USER

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()

    # The poll test creates a real incident (+ its analysis, cache row, and tracked_alarms row
    # referencing it). Other HTTP test files' fixtures delete the whole incidents table at their
    # own setup and don't know about tracked_alarms (it postdates them), so leaving this behind
    # trips their cleanup with a FK violation. Clean up our own rows, innermost FK first, so the
    # rest of the suite is unaffected.
    async with maker() as s:
        await s.execute(delete(TrackedAlarmRow))
        incident_ids = (
            (await s.execute(select(IncidentRow.id).where(IncidentRow.source == "cloudwatch_alarm")))
            .scalars()
            .all()
        )
        if incident_ids:
            analysis_ids = (
                (
                    await s.execute(
                        select(AnalysisRow.id).where(AnalysisRow.incident_id.in_(incident_ids))
                    )
                )
                .scalars()
                .all()
            )
            if analysis_ids:
                await s.execute(
                    delete(AnalysisCacheRow).where(AnalysisCacheRow.analysis_id.in_(analysis_ids))
                )
                await s.execute(delete(AnalysisRow).where(AnalysisRow.id.in_(analysis_ids)))
            await s.execute(delete(IncidentRow).where(IncidentRow.id.in_(incident_ids)))
        await s.commit()

    await engine.dispose()


async def test_create_list_and_delete_sso_connection(client):
    r = await client.post(
        "/api/cloud-connections",
        json={
            "project": "GCM", "env": "prod", "region": "ap-southeast-1",
            "auth_type": "sso", "sso_profile_name": "GCM-Prod-ReadOnlyAccess",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["sso_profile_name"] == "GCM-Prod-ReadOnlyAccess"
    assert created["has_access_key"] is False

    r = await client.get("/api/cloud-connections")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = await client.delete(f"/api/cloud-connections/{created['id']}")
    assert r.status_code == 204
    r = await client.get("/api/cloud-connections")
    assert r.json() == []


async def test_create_access_key_connection_never_returns_the_secret(client):
    r = await client.post(
        "/api/cloud-connections",
        json={
            "project": "GCM", "env": "dev", "region": "us-east-1", "auth_type": "access_key",
            "access_key_id": "AKIAEXAMPLE", "secret_access_key": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_access_key"] is True
    assert "access_key_id" not in body
    assert "secret_access_key" not in body
    assert "encrypted_access_key_id" not in body


async def test_missing_sso_profile_name_is_422(client):
    r = await client.post(
        "/api/cloud-connections",
        json={"project": "GCM", "env": "prod", "region": "us-east-1", "auth_type": "sso"},
    )
    assert r.status_code == 422


async def test_test_connection_endpoint_reports_ok(client):
    r = await client.post(
        "/api/cloud-connections",
        json={"project": "GCM", "env": "prod", "region": "us-east-1", "auth_type": "sso", "sso_profile_name": "p"},
    )
    connection_id = r.json()["id"]
    r = await client.post(f"/api/cloud-connections/{connection_id}/test")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "error": None}


async def test_poll_endpoint_creates_incident_from_alarming_connection(client):
    app.dependency_overrides[get_alarm_fetcher] = lambda: _FakeFetcher(
        alarms=[AlarmState(arn="arn:1", name="cpu-high", state="ALARM", reason="high cpu")]
    )

    r = await client.post(
        "/api/cloud-connections",
        json={"project": "GCM", "env": "prod", "region": "us-east-1", "auth_type": "sso", "sso_profile_name": "p"},
    )
    assert r.status_code == 201

    r = await client.post("/api/cloud-connections/poll")
    assert r.status_code == 200, r.text
    assert r.json() == {"polled": 1, "alarm_count": 1, "errors": 0}

    r = await client.get("/api/incidents")
    assert r.status_code == 200
    incidents = r.json()
    sources = [i["source"] for i in incidents]
    assert "cloudwatch_alarm" in sources

    incident_id = next(i["id"] for i in incidents if i["source"] == "cloudwatch_alarm")
    # Alarm-created incidents wait for a manual "Analyze with AI" trigger — no auto-analysis, so
    # no LLM call/session is even needed at poll time.
    r = await client.get(f"/api/incidents/{incident_id}")
    assert r.json()["status"] == "new"


async def test_update_connection_changes_region(client):
    r = await client.post(
        "/api/cloud-connections",
        json={"project": "GCM", "env": "prod", "region": "us-east-1", "auth_type": "sso", "sso_profile_name": "p"},
    )
    connection_id = r.json()["id"]

    r = await client.patch(
        f"/api/cloud-connections/{connection_id}",
        json={"project": "GCM", "env": "prod", "region": "ap-southeast-1", "auth_type": "sso", "sso_profile_name": "p"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["region"] == "ap-southeast-1"

    r = await client.get("/api/cloud-connections")
    assert r.json()[0]["region"] == "ap-southeast-1"


async def test_update_access_key_connection_without_new_secret_keeps_existing_credentials(client):
    r = await client.post(
        "/api/cloud-connections",
        json={
            "project": "GCM", "env": "dev", "region": "us-east-1", "auth_type": "access_key",
            "access_key_id": "AKIAORIGINAL", "secret_access_key": "original-secret",
        },
    )
    connection_id = r.json()["id"]

    r = await client.patch(
        f"/api/cloud-connections/{connection_id}",
        json={"project": "GCM", "env": "dev", "region": "ap-southeast-1", "auth_type": "access_key"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["region"] == "ap-southeast-1"
    assert body["has_access_key"] is True  # unchanged, not cleared


async def test_update_404_for_unknown_connection(client):
    r = await client.patch(
        "/api/cloud-connections/00000000-0000-0000-0000-000000000000",
        json={"project": "GCM", "env": "prod", "region": "us-east-1", "auth_type": "sso", "sso_profile_name": "p"},
    )
    assert r.status_code == 404


async def test_poll_one_only_polls_that_connection(client):
    r1 = await client.post(
        "/api/cloud-connections",
        json={"project": "GCM", "env": "prod", "region": "us-east-1", "auth_type": "sso", "sso_profile_name": "p1"},
    )
    connection_1 = r1.json()["id"]
    r2 = await client.post(
        "/api/cloud-connections",
        json={"project": "EVP", "env": "dev", "region": "us-west-2", "auth_type": "sso", "sso_profile_name": "p2"},
    )
    connection_2 = r2.json()["id"]

    app.dependency_overrides[get_alarm_fetcher] = lambda: _FakeFetcher(
        alarms=[AlarmState(arn="arn:1", name="cpu-high", state="ALARM")]
    )

    r = await client.post(f"/api/cloud-connections/{connection_1}/poll")
    assert r.status_code == 200, r.text
    assert r.json() == {"polled": 1, "alarm_count": 1, "errors": 0}

    r = await client.get("/api/cloud-connections")
    by_id = {c["id"]: c for c in r.json()}
    assert by_id[connection_1]["last_poll_status"] == "ok"
    assert by_id[connection_1]["last_poll_alarm_count"] == 1
    assert by_id[connection_2]["last_poll_status"] is None  # untouched
    assert by_id[connection_2]["last_poll_alarm_count"] is None


async def test_poll_one_404_for_unknown_connection(client):
    r = await client.post("/api/cloud-connections/00000000-0000-0000-0000-000000000000/poll")
    assert r.status_code == 404


async def test_poll_schedule_reports_configured_interval(client):
    """No scheduler runs under the test client (no lifespan) — next_run_at should come back null
    rather than error, while interval_minutes still reflects config."""
    r = await client.get("/api/cloud-connections/poll-schedule")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["interval_minutes"] == 60
    assert body["next_run_at"] is None
