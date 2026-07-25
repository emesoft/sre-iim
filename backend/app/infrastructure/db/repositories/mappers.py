"""ORM row -> domain entity mappers. Keeps repositories free of field-copying boilerplate and
ensures no ORM type leaks past the infrastructure layer.
"""

from __future__ import annotations

from app.domain.documents.entities import Document
from app.domain.incidents.entities import Analysis, Incident
from app.infrastructure.db.orm import AnalysisRow, DocumentRow, IncidentRow


def incident_to_domain(row: IncidentRow) -> Incident:
    return Incident(
        service=row.service,
        source=row.source,
        fingerprint=row.fingerprint,
        context=row.context,
        status=row.status,
        log_group=row.log_group,
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def analysis_to_domain(row: AnalysisRow) -> Analysis:
    return Analysis(
        incident_id=row.incident_id,
        severity=row.severity,
        summary=row.summary,
        root_cause=row.root_cause,
        recommended_action=row.recommended_action,
        confidence=float(row.confidence) if row.confidence is not None else None,
        cache_state=row.cache_state,
        model_id=row.model_id,
        evidence_chunk_ids=list(row.evidence_chunk_ids or []),
        id=row.id,
        created_at=row.created_at,
    )


def document_to_domain(row: DocumentRow) -> Document:
    return Document(
        title=row.title,
        source_type=row.source_type,
        service=row.service,
        tags=list(row.tags or []),
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
