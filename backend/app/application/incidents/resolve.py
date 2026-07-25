"""ResolveIncident use case: mark an incident resolved and save it as a known-issue case.

Saving the resolution as a `Document` (source_type="incident") reuses the existing RAG chunk/embed/
index pipeline (same shape as `IngestDocument`) — a later similar incident then surfaces it through
the same retrieval path `RagAnalyzer` already runs, no separate matching mechanism needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.documents.chunking import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunk_markdown
from app.domain.documents.entities import Document, EmbeddedChunk
from app.domain.documents.ports import DocumentRepository, Embedder
from app.domain.incidents.entities import Analysis, Incident
from app.domain.incidents.ports import IncidentRepository
from app.domain.shared import UnitOfWork


class NoAnalysisToResolveError(ValueError):
    """The incident has no analysis yet, so there's nothing to save as a known-issue case."""


def _case_content(incident: Incident, analysis: Analysis, resolution_notes: str) -> str:
    return (
        f"# {incident.service}: {analysis.summary}\n\n"
        f"## Root cause\n{analysis.root_cause}\n\n"
        f"## Recommended action\n{analysis.recommended_action}\n\n"
        f"## Resolution\n{resolution_notes}"
    )


@dataclass
class ResolveIncident:
    incidents: IncidentRepository
    documents: DocumentRepository
    embedder: Embedder
    uow: UnitOfWork
    chunk_size: int = DEFAULT_CHUNK_SIZE
    overlap: int = DEFAULT_OVERLAP

    async def resolve(self, incident_id: uuid.UUID, *, resolution_notes: str) -> Incident:
        incident = await self.incidents.get(incident_id)
        if incident is None:
            raise ValueError(f"incident {incident_id} not found")
        analysis = await self.incidents.latest_analysis(incident_id)
        if analysis is None:
            raise NoAnalysisToResolveError(
                f"incident {incident_id} has no analysis to save as a known-issue case"
            )

        content = _case_content(incident, analysis, resolution_notes)
        texts = chunk_markdown(content, self.chunk_size, self.overlap)
        vectors = await self.embedder.embed_documents(texts)
        chunks = [
            EmbeddedChunk(index=i, content=text, embedding=vector)
            for i, (text, vector) in enumerate(zip(texts, vectors))
        ]
        await self.documents.add(
            Document(
                title=f"{incident.service}: {analysis.summary}",
                source_type="incident",
                service=incident.service,
                incident_id=incident.id,
            ),
            chunks,
        )

        await self.incidents.set_status(incident_id, "resolved")
        incident.status = "resolved"
        await self.uow.commit()
        return incident
