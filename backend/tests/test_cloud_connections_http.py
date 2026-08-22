"""End-to-end HTTP tests for /api/cloud-connections against real Postgres.

Overrides get_alarm_fetcher with a fake so no real AWS call happens (same pattern as
test_documents_http.py overriding get_embedder). The poll test overrides get_base_analyzer and
get_embedder with fakes (same pattern test_incidents_http.py uses), not get_analyzer directly:
alarm analysis now runs in the background via `resolve_background_incident_deps` (see
app/interface/http/deps.py's `_analyze_incident_in_background`, added to fix the session-lifecycle
race), which only honors overrides for get_session/get_base_analyzer/get_embedder — not
get_analyzer itself — so this is the override combination that actually reaches the background
analysis path.
"""

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cloud_connections.entities import AlarmState
from app.domain.incidents.entities import AnalysisDraft
from app.infrastructure.db.orm import (
    EMBED_DIM,
    AnalysisCacheRow,
    AnalysisRow,
    Base,
    CloudConnectionRow,
    IncidentRow,
    TrackedAlarmRow,
)
from app.infrastructure.security.encryptor import Encryptor
from app.interface.http.deps import (
    get_alarm_fetcher,
    get_base_analyzer,
    get_embedder,
    get_encryptor,
    get_session,
)
from app.main import app

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


class _FakeAnalyzer:
    """Stands in for the real Bedrock/DeepSeek base analyzer so the poll test doesn't need live
    LLM credentials — mirrors test_ingest_usecase.py's CountingAnalyzer fake."""

    async def analyze(self, context, evidence=None, reporter=None):
        return AnalysisDraft(
            severity="critical",
            summary="CloudWatch alarm firing",
            root_cause="unknown (fake analyzer)",
            recommended_action="investigate the alarm",
            confidence="high",
            model_id="fake-model",
        )


class _FakeEmbedder:
    """Stands in for the real embedder so the background analysis path (RagAnalyzer, via
    resolve_background_incident_deps) doesn't need a live embedding call."""

    async def embed_documents(self, texts):
        return [[0.1] * EMBED_DIM for _ in texts]

    async def embed_query(self, text):
        return [0.1] * EMBED_DIM


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
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_alarm_fetcher] = lambda: _FakeFetcher()
    app.dependency_overrides[get_encryptor] = lambda: Encryptor(_TEST_ENCRYPTION_KEY)

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


async def _await_status(client, incident_id: str, *, timeout_s: float = 5.0) -> str:
    """PollAlarmsJob has no event bus (unlike POST /api/incidents' SSE stream), so there's no
    push notification for the background analysis finishing - poll GET until the incident leaves
    "analyzing", proving it doesn't get stuck there forever (the bug this test guards against)."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        r = await client.get(f"/api/incidents/{incident_id}")
        status_ = r.json()["status"]
        if status_ != "analyzing":
            return status_
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"incident {incident_id} stuck at 'analyzing'")
        await asyncio.sleep(0.05)


async def test_poll_endpoint_creates_incident_from_alarming_connection(client):
    app.dependency_overrides[get_alarm_fetcher] = lambda: _FakeFetcher(
        alarms=[AlarmState(arn="arn:1", name="cpu-high", state="ALARM", reason="high cpu")]
    )
    # Alarm analysis runs in the background via resolve_background_incident_deps, which only
    # honors overrides for get_session/get_base_analyzer/get_embedder (not get_analyzer itself).
    app.dependency_overrides[get_base_analyzer] = lambda: _FakeAnalyzer()
    app.dependency_overrides[get_embedder] = lambda: _FakeEmbedder()

    r = await client.post(
        "/api/cloud-connections",
        json={"project": "GCM", "env": "prod", "region": "us-east-1", "auth_type": "sso", "sso_profile_name": "p"},
    )
    assert r.status_code == 201

    r = await client.post("/api/cloud-connections/poll")
    assert r.status_code == 200, r.text
    assert r.json() == {"polled": 1}

    r = await client.get("/api/incidents")
    assert r.status_code == 200
    incidents = r.json()
    sources = [i["source"] for i in incidents]
    assert "cloudwatch_alarm" in sources

    incident_id = next(i["id"] for i in incidents if i["source"] == "cloudwatch_alarm")
    # Regression check for the session-lifecycle bug: the background analysis must complete
    # (using its own independent session) instead of leaving the incident stuck at "analyzing".
    assert await _await_status(client, incident_id) == "analyzed"
