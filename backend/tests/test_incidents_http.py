"""End-to-end HTTP tests for the incident endpoints against a real Postgres.

Overrides the interface-layer `get_session` and `get_analyzer` dependencies (no Bedrock call), then
drives the real FastAPI app through the interface -> application -> infrastructure layers. Skipped
when no database is reachable.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.ado_connections.entities import AdoConnection
from app.domain.incidents.entities import AnalysisDraft
from app.domain.users.entities import User
from app.infrastructure.db.orm import (
    EMBED_DIM,
    AnalysisCacheRow,
    AnalysisRow,
    Base,
    DocChunkRow,
    DocumentRow,
    IncidentRow,
)
from app.interface.http.deps import (
    get_ado_connection_repository,
    get_ado_ticket_client_factory,
    get_base_analyzer,
    get_current_user,
    get_embedder,
    get_session,
)
from app.main import app
from tests.sse_test_utils import iter_sse

# incidents.py is gated (require_role) now that per-user auth exists — override get_current_user
# with a fake admin so these tests exercise the incident workflow itself, not the role gate (that's
# covered by test_auth_http.py / test_users_http.py).
_ADMIN_USER = User(
    id=uuid.uuid4(),
    email="admin@test.local",
    password_hash="unused",
    role="admin",
    created_at=datetime.now(timezone.utc),
)

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)

_CTX = {
    "service": "GCM",
    "sample_logs": [{"message": "java.lang.OutOfMemoryError: Java heap space"}],
    "recent_deploy": {"version": "1.8.0"},
}


class _FakeAnalyzer:
    async def analyze(self, context: dict, evidence=None) -> AnalysisDraft:
        return AnalysisDraft(
            severity="critical",
            summary="GCM OOM after deploy",
            root_cause="heap regression in 1.8.0",
            recommended_action="roll back GCM",
            confidence="high",
            model_id="test-model",
        )


class _FakeEmbedder:
    async def embed_documents(self, texts):
        return [[0.1] * EMBED_DIM for _ in texts]

    async def embed_query(self, text):
        return [0.1] * EMBED_DIM


class _FakeTicketClient:
    async def create_ticket(self, title, description, *, related_url=None):
        return "https://dev.azure.com/fake-org/fake-project/_workitems/edit/123"


class _FakeAdoConnectionRepo:
    """Every project has an (unused, fake) ADO connection configured — the tests exercise the
    ticket-creation flow itself, not per-project ADO configuration."""

    async def get_by_project(self, project):
        return AdoConnection(
            id=uuid.uuid4(), project=project, org="fake-org", ado_project="fake-project",
            encrypted_pat="unused",
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
        await s.execute(delete(AnalysisCacheRow))
        await s.execute(delete(AnalysisRow))
        await s.execute(delete(DocChunkRow))
        await s.execute(delete(DocumentRow))  # may reference incidents.id (known-issue cases)
        await s.execute(delete(IncidentRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_base_analyzer] = lambda: _FakeAnalyzer()
    app.dependency_overrides[get_embedder] = lambda: _FakeEmbedder()
    app.dependency_overrides[get_ado_connection_repository] = lambda: _FakeAdoConnectionRepo()
    app.dependency_overrides[get_current_user] = lambda: _ADMIN_USER
    app.dependency_overrides[get_ado_ticket_client_factory] = lambda: (
        lambda connection: _FakeTicketClient()
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def _await_analyzed(client, incident_id: str) -> None:
    """Drain the incident's SSE stream to its terminal event — deterministic, no sleep-polling —
    so assertions on its analysis can run once the background analysis has actually finished."""
    async with client.stream("GET", f"/api/incidents/{incident_id}/stream") as resp:
        events = [e async for e in iter_sse(resp)]
    assert events[-1][0] == "analyzed", events


async def test_post_get_and_cache_hit(client):
    r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    assert r.status_code == 201, r.text
    created = r.json()
    incident_id = created["incident_id"]
    assert created["status"] == "analyzing"
    assert created["stream"] == f"/api/incidents/{incident_id}/stream"
    await _await_analyzed(client, incident_id)

    r = await client.get(f"/api/incidents/{incident_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["context"]["service"] == "GCM"
    analysis = detail["analysis"]
    assert analysis["_cache"] == "MISS"
    assert analysis["severity"] == "critical"
    assert analysis["confidence"] == pytest.approx(0.9)
    assert analysis["evidence"] == []

    r2 = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    assert r2.status_code == 201
    await _await_analyzed(client, r2.json()["incident_id"])
    r2_detail = await client.get(f"/api/incidents/{r2.json()['incident_id']}")
    assert r2_detail.json()["analysis"]["_cache"] == "HIT"

    r = await client.get("/api/incidents")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    assert items[0]["severity"] == "critical"
    assert items[0]["summary"] == "GCM OOM after deploy"

    r = await client.get("/api/incidents", params={"severity": "critical"})
    assert len(r.json()) == 2
    r = await client.get("/api/incidents", params={"severity": "info"})
    assert r.json() == []


async def test_missing_service_returns_422(client):
    r = await client.post("/api/incidents", json={"source": "manual", "context": {}})
    assert r.status_code == 422
    assert r.json()["detail"] == "context.service is required"


async def test_list_headline_extracts_quoted_alarm_name_from_alert(client):
    r = await client.post(
        "/api/incidents",
        json={
            "source": "cloudwatch_alarm",
            "context": {
                "service": "rxdevs",
                "alert": "CloudWatch alarm 'ecs-easyrx-prod-svc-AlarmLow' is in ALARM state: "
                "Threshold Crossed.",
            },
        },
    )
    assert r.status_code == 201

    r = await client.get("/api/incidents")
    assert r.status_code == 200
    item = next(i for i in r.json() if i["source"] == "cloudwatch_alarm")
    assert item["headline"] == "ecs-easyrx-prod-svc-AlarmLow"


async def test_list_headline_strips_cluster_prefix_and_uuid_suffix(client):
    r = await client.post(
        "/api/incidents",
        json={
            "source": "cloudwatch_alarm",
            "context": {
                "service": "rxdevs",
                "alert": (
                    "CloudWatch alarm 'TargetTracking-service/ecs-easyrx-prod-cluster/"
                    "ecs-easyrx-prod-rocketshipit-svc-AlarmLow-b90a64fc-e73f-47a9-89b2-"
                    "89aacf4fe5c1' is in ALARM state: Threshold Crossed."
                ),
            },
        },
    )
    assert r.status_code == 201

    r = await client.get("/api/incidents")
    item = next(i for i in r.json() if i["source"] == "cloudwatch_alarm")
    assert item["headline"] == "ecs-easyrx-prod-rocketshipit-svc-AlarmLow"


async def test_list_headline_is_null_without_an_alert(client):
    r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    assert r.status_code == 201

    r = await client.get("/api/incidents")
    item = next(i for i in r.json() if i["id"] == r.json()[0]["id"])
    assert item["headline"] is None


async def test_env_appears_in_list_and_detail_when_present_in_context(client):
    r = await client.post(
        "/api/incidents",
        json={
            "source": "cloudwatch_alarm",
            "context": {"service": "rxdevs", "env": "dev", "alert": "CloudWatch alarm 'x'"},
        },
    )
    incident_id = r.json()["incident_id"]

    r = await client.get("/api/incidents")
    item = next(i for i in r.json() if i["id"] == incident_id)
    assert item["env"] == "dev"

    r = await client.get(f"/api/incidents/{incident_id}")
    assert r.json()["env"] == "dev"
    assert r.json()["headline"] == "x"


async def test_env_is_null_without_one_in_context(client):
    r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    incident_id = r.json()["incident_id"]

    r = await client.get(f"/api/incidents/{incident_id}")
    assert r.json()["env"] is None


async def test_list_returns_one_row_per_incident_even_when_reanalyzed(client):
    """A second `/analyze` call on an already-analyzed incident adds another Analysis row (this
    is exactly how the real demo produced 3 duplicate rows for one incident in the sidebar) — the
    list must still return exactly one row per incident, keyed to its latest analysis."""
    r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    incident_id = r.json()["incident_id"]
    await _await_analyzed(client, incident_id)

    r = await client.post(f"/api/incidents/{incident_id}/analyze")
    assert r.status_code == 200, r.text
    await _await_analyzed(client, incident_id)

    r = await client.get("/api/incidents")
    assert r.status_code == 200
    matching = [i for i in r.json() if i["id"] == incident_id]
    assert len(matching) == 1


async def test_resolve_saves_case_and_next_similar_incident_flags_known_issue(client):
    r1 = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    incident1_id = r1.json()["incident_id"]
    await _await_analyzed(client, incident1_id)

    resolve = await client.post(
        f"/api/incidents/{incident1_id}/resolve",
        json={"resolution_notes": "Rolled back to 1.7.9 and bumped container heap size."},
    )
    assert resolve.status_code == 200, resolve.text
    assert resolve.json()["status"] == "resolved"

    other_ctx = {**_CTX, "sample_logs": [{"message": "java.lang.OutOfMemoryError: different trace"}]}
    r2 = await client.post("/api/incidents", json={"source": "manual", "context": other_ctx})
    incident2_id = r2.json()["incident_id"]
    await _await_analyzed(client, incident2_id)

    detail2 = (await client.get(f"/api/incidents/{incident2_id}")).json()
    known_issue = detail2["analysis"]["known_issue"]
    assert known_issue is not None
    assert known_issue["incident_id"] == incident1_id
    assert known_issue["similarity"] == pytest.approx(1.0, abs=1e-6)


async def test_create_ticket_sets_url_and_status(client):
    r = await client.post("/api/incidents", json={"source": "manual", "context": _CTX})
    incident_id = r.json()["incident_id"]
    await _await_analyzed(client, incident_id)

    ticket = await client.post(f"/api/incidents/{incident_id}/ticket")
    assert ticket.status_code == 200, ticket.text
    body = ticket.json()
    assert body["status"] == "ticketed"
    assert body["ticket_url"] == "https://dev.azure.com/fake-org/fake-project/_workitems/edit/123"

    detail = (await client.get(f"/api/incidents/{incident_id}")).json()
    assert detail["ticket_url"] == body["ticket_url"]
    assert detail["status"] == "ticketed"


async def test_create_ticket_404_for_unknown_incident(client):
    r = await client.post("/api/incidents/00000000-0000-0000-0000-000000000000/ticket")
    assert r.status_code == 404


async def test_resolve_404_for_unknown_incident(client):
    r = await client.post(
        "/api/incidents/00000000-0000-0000-0000-000000000000/resolve",
        json={"resolution_notes": "n/a"},
    )
    assert r.status_code == 404


async def test_analyze_endpoint_runs_analysis_for_a_new_incident(client):
    # Simulates a CloudWatch-alarm-created incident: status="new", no analysis yet — inserted
    # directly since there's no public endpoint that creates one without also analyzing it.
    engine = create_async_engine(_DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    incident_id = uuid.uuid4()
    async with maker() as s:
        s.add(
            IncidentRow(
                id=incident_id, service="EVP", source="cloudwatch_alarm",
                fingerprint="fp-1", context=_CTX, status="new",
            )
        )
        await s.commit()
    await engine.dispose()

    r = await client.post(f"/api/incidents/{incident_id}/analyze")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "analyzing"
    assert body["stream"] == f"/api/incidents/{incident_id}/stream"
    await _await_analyzed(client, str(incident_id))

    r = await client.get(f"/api/incidents/{incident_id}")
    assert r.json()["analysis"]["severity"] == "critical"


async def test_analyze_404_for_unknown_incident(client):
    r = await client.post("/api/incidents/00000000-0000-0000-0000-000000000000/analyze")
    assert r.status_code == 404
