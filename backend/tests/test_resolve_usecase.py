"""Unit tests for ResolveIncident using in-memory fakes — no DB, no network.

Covers the "resolve without analysis" behavior: an incident that was never analyzed (or had its
analysis cleared) has nothing to write up as a known-issue case, so resolving it just closes the
incident out instead of requiring an analysis first.
"""

import uuid

import pytest

from app.application.incidents.resolve import ResolveIncident
from app.domain.documents.entities import Document
from app.domain.incidents.entities import Analysis, Incident

pytestmark = pytest.mark.asyncio


def _incident(**overrides) -> Incident:
    fields = {
        "id": uuid.uuid4(),
        "service": "gcm",
        "source": "manual",
        "fingerprint": "fp",
        "context": {"service": "gcm"},
        "status": "analyzed",
        **overrides,
    }
    return Incident(**fields)


def _analysis(incident_id) -> Analysis:
    return Analysis(
        incident_id=incident_id,
        severity="high",
        summary="ECS OOM",
        root_cause="memory leak",
        recommended_action="restart",
        confidence=0.9,
        cache_state="MISS",
        model_id="test-model",
    )


class FakeIncidentRepo:
    def __init__(self, incident, analysis=None):
        self._incident = incident
        self._analysis = analysis
        self.set_status_calls = []

    async def get(self, incident_id):
        return self._incident if incident_id == self._incident.id else None

    async def latest_analysis(self, incident_id):
        return self._analysis

    async def set_status(self, incident_id, status, *, error_message=None):
        self.set_status_calls.append((incident_id, status))


class FakeDocumentRepo:
    def __init__(self):
        self.added: list[Document] = []

    async def add(self, document, chunks):
        self.added.append(document)
        return document


class FakeEmbedder:
    async def embed_documents(self, texts):
        return [[0.0] * 4 for _ in texts]

    async def embed_query(self, text):
        return [0.0] * 4


class FakeUnitOfWork:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


async def test_resolve_with_analysis_saves_a_known_issue_document():
    incident = _incident()
    analysis = _analysis(incident.id)
    documents = FakeDocumentRepo()
    resolver = ResolveIncident(
        incidents=FakeIncidentRepo(incident, analysis),
        documents=documents,
        embedder=FakeEmbedder(),
        uow=FakeUnitOfWork(),
    )
    result = await resolver.resolve(incident.id, resolution_notes="restarted the task")
    assert result.status == "resolved"
    assert len(documents.added) == 1
    assert documents.added[0].source_type == "incident"


async def test_resolve_without_analysis_still_closes_the_incident():
    incident = _incident(status="new")
    documents = FakeDocumentRepo()
    incidents = FakeIncidentRepo(incident, analysis=None)
    resolver = ResolveIncident(
        incidents=incidents, documents=documents, embedder=FakeEmbedder(), uow=FakeUnitOfWork()
    )
    result = await resolver.resolve(incident.id, resolution_notes="alarm cleared on its own")
    assert result.status == "resolved"
    assert incidents.set_status_calls == [(incident.id, "resolved")]
    # Nothing to write up as a known-issue case — no document created.
    assert documents.added == []


async def test_resolve_unknown_incident_raises():
    incident = _incident()
    resolver = ResolveIncident(
        incidents=FakeIncidentRepo(incident), documents=FakeDocumentRepo(),
        embedder=FakeEmbedder(), uow=FakeUnitOfWork(),
    )
    with pytest.raises(ValueError, match="not found"):
        await resolver.resolve(uuid.uuid4(), resolution_notes="x")
