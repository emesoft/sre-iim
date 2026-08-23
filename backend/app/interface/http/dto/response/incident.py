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


class ChatMessageOut(BaseModel):
    """One row in `GET /api/incidents/{id}/chat`, and the response of `POST .../chat`."""

    id: uuid.UUID
    role: str  # user | assistant
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime
