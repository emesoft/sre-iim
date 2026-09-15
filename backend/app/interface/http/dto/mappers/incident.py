"""Mappers: incident domain entities -> response DTOs."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.domain.documents.entities import EvidenceRef
from app.domain.incidents.entities import Analysis, ChatMessage, Incident, IncidentRollup
from app.interface.http.dto.response.incident import (
    AnalysisOut,
    ChatMessageOut,
    IncidentDetail,
    IncidentRollupOut,
    IncidentSummary,
    KnownIssueOut,
    NoisyAlarmOut,
    ProjectRollupOut,
)

#: A guard against a pathological alert body, not a layout decision.
#:
#: It was 80, chosen when the incident list was a 340px column. That column is now a full-width
#: table and the detail pane is wider still, and both truncate with CSS at whatever width they
#: actually have — so cutting here only meant the *detail view* showed a clipped title too, with
#: the rest of the sentence unavailable anywhere. Long enough for the alert texts real systems
#: emit; short enough that a runaway stack trace can't become a headline.
_HEADLINE_MAX_LEN = 240
_QUOTED = re.compile(r"'([^']+)'")
# CloudWatch auto-generates alarm names like
# "TargetTracking-service/<cluster>/<service>-AlarmLow-b90a64fc-e73f-47a9-89b2-89aacf4fe5c1" — the
# leading "policy-type/cluster/" prefix and trailing UUID are noise for a list headline.
_UUID_SUFFIX = re.compile(
    r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def build_headline(context: dict) -> str | None:
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
        headline=build_headline(incident.context),
        env=incident.context.get("env"),
        occurrence_count=incident.occurrence_count,
        previous_incident_id=incident.previous_incident_id,
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
        headline=build_headline(incident.context),
        env=incident.context.get("env"),
        error_message=incident.error_message,
        occurrence_count=incident.occurrence_count,
        previous_incident_id=incident.previous_incident_id,
    )


def chat_message_out(message: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        input_tokens=message.input_tokens,
        cached_input_tokens=message.cached_input_tokens,
        output_tokens=message.output_tokens,
        created_at=message.created_at,
    )


def incident_rollup(rollup: IncidentRollup) -> IncidentRollupOut:
    return IncidentRollupOut(
        active=rollup.active,
        urgent=rollup.urgent,
        untriaged=rollup.untriaged,
        new_last_24h=rollup.new_last_24h,
        lanes=dict(rollup.lanes),
        all_projects=list(rollup.all_projects),
        projects=[
            ProjectRollupOut(
                project=p.project,
                open=p.open,
                urgent=p.urgent,
                untriaged=p.untriaged,
                top_incident_id=p.top_incident_id,
                # Same headline rule as every incident row, so a project's "top" reads identically
                # to that incident's own row in the list.
                top_headline=build_headline(p.top_context or {}),
            )
            for p in rollup.projects
        ],
        noisy=[
            NoisyAlarmOut(
                service=n.service,
                fingerprint=n.fingerprint,
                count=n.count,
                # Falls back to the fingerprint's error-signature segment: a manually-ingested
                # incident has no `alert` text for build_headline to quote.
                label=build_headline(n.context or {}) or _fingerprint_label(n.fingerprint),
            )
            for n in rollup.noisy
        ],
    )


def _fingerprint_label(fingerprint: str) -> str:
    """`service | error-signature | deploy-version` (see domain/incidents/fingerprint.py) — the
    middle segment is the only human-readable part."""
    parts = [p.strip() for p in fingerprint.split("|")]
    signature = parts[1] if len(parts) > 1 and parts[1] else fingerprint
    return signature if len(signature) <= _HEADLINE_MAX_LEN else signature[: _HEADLINE_MAX_LEN - 1] + "…"
