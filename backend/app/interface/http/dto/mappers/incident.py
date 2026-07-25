"""Mappers: incident domain entities -> response DTOs."""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.documents.entities import EvidenceRef
from app.domain.incidents.entities import Analysis, Incident
from app.interface.http.dto.response.incident import (
    AnalysisOut,
    IncidentDetail,
    IncidentSummary,
    KnownIssueOut,
)


def analysis_out(analysis: Analysis, evidence: Sequence[EvidenceRef] | None = None) -> AnalysisOut:
    return AnalysisOut(
        severity=analysis.severity,
        summary=analysis.summary,
        root_cause=analysis.root_cause,
        recommended_action=analysis.recommended_action,
        confidence=analysis.confidence,
        model_id=analysis.model_id,
        cache_state=analysis.cache_state,
        evidence=[
            {"chunk_id": str(r.chunk_id), "source_type": r.source_type, "title": r.title}
            for r in (evidence or [])
        ],
        known_issue=(
            KnownIssueOut(
                incident_id=analysis.known_issue_incident_id,
                similarity=analysis.known_issue_similarity,
            )
            if analysis.known_issue_incident_id is not None
            else None
        ),
    )


def incident_summary(incident: Incident, analysis: Analysis | None) -> IncidentSummary:
    return IncidentSummary(
        id=incident.id,
        service=incident.service,
        source=incident.source,
        status=incident.status,
        fingerprint=incident.fingerprint,
        created_at=incident.created_at,
        severity=analysis.severity if analysis else None,
        summary=analysis.summary if analysis else None,
    )


def incident_detail(
    incident: Incident,
    analysis: Analysis | None,
    evidence: Sequence[EvidenceRef] | None = None,
) -> IncidentDetail:
    return IncidentDetail(
        id=incident.id,
        service=incident.service,
        source=incident.source,
        status=incident.status,
        fingerprint=incident.fingerprint,
        context=incident.context,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        log_group=incident.log_group,
        analysis=analysis_out(analysis, evidence) if analysis else None,
    )
