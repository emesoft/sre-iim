"""Incident response DTOs — pure serialization schemas (mapping lives in dto/mappers)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class IncidentCreatedResponse(BaseModel):
    """`POST /api/incidents` response (SPEC 6.5)."""

    incident_id: uuid.UUID
    status: str
    stream: str


class KnownIssueOut(BaseModel):
    """A past resolved incident this analysis matched, above the known-issue similarity threshold."""

    incident_id: uuid.UUID
    similarity: float


class AnalysisOut(BaseModel):
    """The persisted 5-field analysis plus cache state and evidence refs."""

    severity: str
    summary: str
    root_cause: str
    recommended_action: str
    confidence: float | None
    model_id: str
    cache_state: str = Field(serialization_alias="_cache")  # HIT | MISS
    evidence: list[dict] = Field(default_factory=list)
    known_issue: KnownIssueOut | None = None
    # None means "not tracked for this provider" (Bedrock/DeepSeek); a cache HIT is explicitly 0.
    input_tokens: int | None = None
    output_tokens: int | None = None


class IncidentSummary(BaseModel):
    """One row in `GET /api/incidents` (incident + its analysis headline)."""

    id: uuid.UUID
    service: str
    source: str
    status: str
    fingerprint: str
    created_at: datetime
    severity: str | None = None
    summary: str | None = None
    # Short human-readable signal extracted from raw context (e.g. the CloudWatch alarm name) —
    # every incident from the same connection shares `service`, so the list needs something more
    # specific to tell rows apart before an AI summary exists.
    headline: str | None = None
    # From context.env (set by PollAlarmsJob from the connection's env) — None for incidents
    # created before this field existed, or created without a cloud connection.
    env: str | None = None
    # How many times the same CloudWatch alarm has fired into a new incident, counting this one —
    # 1 for a first-time/non-alarm incident. previous_incident_id links to the incident this alarm
    # opened last time, so the UI can offer a "view previous occurrence" link.
    occurrence_count: int = 1
    previous_incident_id: uuid.UUID | None = None


class ProjectRollupOut(BaseModel):
    """One row of the dashboard's project board."""

    project: str
    open: int
    urgent: int
    untriaged: int
    top_incident_id: uuid.UUID | None = None
    top_headline: str | None = None


class NoisyAlarmOut(BaseModel):
    """One repeatedly-firing problem in the rollup window."""

    service: str
    fingerprint: str
    count: int
    label: str


class IncidentRollupOut(BaseModel):
    """`GET /api/incidents/rollup` — every number the dashboard shows, aggregated in SQL.

    The dashboard previously derived its counts from the first page of `GET /api/incidents`, so
    they went wrong past that page's 50-row cap and could never be broken down per project."""

    active: int
    urgent: int
    untriaged: int
    new_last_24h: int
    #: Triage lane -> how many incidents are in it. The tab counts on the Incidents page read this
    #: rather than counting a page of rows, which would be wrong past the page cap.
    lanes: dict[str, int] = Field(default_factory=dict)
    #: Options for the project filter — unaffected by the filter itself.
    all_projects: list[str] = Field(default_factory=list)
    projects: list[ProjectRollupOut] = Field(default_factory=list)
    noisy: list[NoisyAlarmOut] = Field(default_factory=list)


class BulkResultOut(BaseModel):
    """`POST /api/incidents/bulk` — what actually happened, not just "ok".

    `skipped` is ids the caller couldn't see or that no longer exist; reporting it as a number
    keeps one stale selection from silently looking like a full success."""

    action: str
    done: int
    skipped: int


class IncidentDetail(BaseModel):
    """`GET /api/incidents/{id}`: incident + context + analysis + evidence."""

    id: uuid.UUID
    service: str
    source: str
    status: str
    fingerprint: str
    context: dict
    created_at: datetime
    updated_at: datetime
    log_group: str | None = None
    ticket_url: str | None = None
    analysis: AnalysisOut | None = None
    # See IncidentSummary — same fields, repeated here since the detail view doesn't otherwise
    # derive them from `context` itself.
    headline: str | None = None
    env: str | None = None
    # The reason the last analysis attempt failed (status == "failed"); persisted so it survives
    # a page reload, not just visible to whoever was watching the SSE stream live.
    error_message: str | None = None
    # See IncidentSummary.
    occurrence_count: int = 1
    previous_incident_id: uuid.UUID | None = None


class ChatMessageOut(BaseModel):
    """One row in `GET /api/incidents/{id}/chat`, and the response of `POST .../chat`."""

    id: uuid.UUID
    role: str  # user | assistant
    content: str
    #: Fresh input only. The cached prefix — system prompt, incident context, the transcript so
    #: far — is reported separately: it is re-read on every turn and costs a fraction of the price,
    #: so one combined figure looks alarming and means very little.
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime
