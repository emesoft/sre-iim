"""Mappers: incident domain entities -> response DTOs."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.domain.documents.entities import EvidenceRef
from app.domain.incidents.entities import Analysis, Incident
from app.interface.http.dto.response.incident import (
    AnalysisOut,
    IncidentDetail,
    IncidentSummary,
    KnownIssueOut,
)

_HEADLINE_MAX_LEN = 80
_QUOTED = re.compile(r"'([^']+)'")
# CloudWatch auto-generates alarm names like
# "TargetTracking-service/<cluster>/<service>-AlarmLow-b90a64fc-e73f-47a9-89b2-89aacf4fe5c1" — the
# leading "policy-type/cluster/" prefix and trailing UUID are noise for a list headline.
_UUID_SUFFIX = re.compile(
    r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _headline(context: dict) -> str | None:
    """Pull a short human-readable signal out of raw context for the incidents list — every
    incident from one connection shares `service`, so distinguishing rows before an AI summary
    exists needs something more specific. CloudWatch's `alert` text quotes the alarm name
    (`"CloudWatch alarm 'foo-bar' is in ALARM state: ..."`); reuse that quoted name when present,
    otherwise fall back to the raw alert text itself."""
    alert = context.get("alert")
    if not alert:
        return None
    match = _QUOTED.search(str(alert))
    text = match.group(1) if match else str(alert)
    text = text.rsplit("/", 1)[-1]
    text = _UUID_SUFFIX.sub("", text)
    return text if len(text) <= _HEADLINE_MAX_LEN else text[: _HEADLINE_MAX_LEN - 1] + "…"


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
        input_tokens=analysis.input_tokens,
        output_tokens=analysis.output_tokens,
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
        headline=_headline(incident.context),
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
        ticket_url=incident.ticket_url,
        analysis=analysis_out(analysis, evidence) if analysis else None,
    )
