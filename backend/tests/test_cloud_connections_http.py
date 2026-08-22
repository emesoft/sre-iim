"""End-to-end HTTP tests for /api/cloud-connections against real Postgres.

Overrides get_alarm_fetcher with a fake so no real AWS call happens (same pattern as
test_documents_http.py overriding get_embedder).

The poll test also overrides get_poll_alarms_job with a fake analyzer. `get_poll_alarms_job`
(app/interface/http/deps.py) builds its IngestIncident by calling `get_base_analyzer()` /
`get_embedder()` / `get_analyzer()` directly as plain function calls rather than as `Depends(...)`
parameters, so `app.dependency_overrides[get_base_analyzer]` (the mechanism used for
get_session/get_alarm_fetcher) never reaches them — confirmed empirically: overriding those left
the poll endpoint still calling real Bedrock and failing on `ValidationException: The provided
model identifier is invalid` in this environment. Overriding `get_poll_alarms_job` itself is the
one override point that FastAPI's DI actually reaches, since the route depends on it via
`Depends(get_poll_alarms_job)`.
"""

import os

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.cloud_connections.poll_alarms import PollAlarmsJob
from app.application.incidents.ingest import IngestIncident
from app.application.incidents.resolve import ResolveIncident
from app.domain.cloud_connections.entities import AlarmState
from app.domain.incidents.entities import AnalysisDraft
from app.infrastructure.clock import SystemClock
from app.infrastructure.db.orm import (
    AnalysisCacheRow,
    AnalysisRow,
    Base,
    CloudConnectionRow,
    IncidentRow,
    TrackedAlarmRow,
)
from app.infrastructure.db.repositories import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyCloudConnectionRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyIncidentRepository,
    SqlAlchemyTrackedAlarmRepository,
    SqlAlchemyUnitOfWork,
)
from app.infrastructure.config import get_settings
from app.interface.http.deps import (
    get_alarm_fetcher,
    get_embedder,
    get_poll_alarms_job,
    get_session,
)
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)


class _FakeFetcher:
    def __init__(self, alarms=None, should_fail=False):
        self._alarms = alarms or []
        self._should_fail = should_fail

    async def list_alarms(self, connection):
        if self._should_fail:
            raise RuntimeError("access denied")
        return self._alarms


class _FakeAnalyzer:
    """Stands in for the real Bedrock/DeepSeek analyzer so the poll test doesn't need live LLM
    credentials — mirrors test_ingest_usecase.py's CountingAnalyzer fake."""

    async def analyze(self, context, evidence=None, reporter=None):
        return AnalysisDraft(
            severity="critical",
            summary="CloudWatch alarm firing",
            root_cause="unknown (fake analyzer)",
            recommended_action="investigate the alarm",
            confidence="high",
            model_id="fake-model",
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
        await s.execute(delete(TrackedAlarmRow))
        await s.execute(delete(CloudConnectionRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_alarm_fetcher] = lambda: _FakeFetcher()

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

    def _fake_poll_alarms_job(session=Depends(get_session), fetcher=Depends(get_alarm_fetcher)):
        settings = get_settings()
        return PollAlarmsJob(
            connections=SqlAlchemyCloudConnectionRepository(session),
            tracked=SqlAlchemyTrackedAlarmRepository(session),
            fetcher=fetcher,
            ingest=IngestIncident(
                incidents=SqlAlchemyIncidentRepository(session),
                cache=SqlAlchemyAnalysisCacheRepository(session),
                analyzer=_FakeAnalyzer(),
                clock=SystemClock(),
                uow=SqlAlchemyUnitOfWork(session),
                cache_ttl_seconds=settings.cache_ttl_seconds,
            ),
            resolve=ResolveIncident(
                incidents=SqlAlchemyIncidentRepository(session),
                documents=SqlAlchemyDocumentRepository(session),
                embedder=get_embedder(),
                uow=SqlAlchemyUnitOfWork(session),
            ),
            uow=SqlAlchemyUnitOfWork(session),
        )

    app.dependency_overrides[get_poll_alarms_job] = _fake_poll_alarms_job

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
    sources = [i["source"] for i in r.json()]
    assert "cloudwatch_alarm" in sources
