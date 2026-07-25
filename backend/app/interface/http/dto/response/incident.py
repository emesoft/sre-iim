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


class LogEventOut(BaseModel):
    """One log line returned by a log search."""

    timestamp: datetime
    message: str


class LogSearchResult(BaseModel):
    """`POST /api/incidents/{id}/logs/search` response: fetched log lines + the re-run analysis."""

    log_group: str
    log_events: list[LogEventOut]
    analysis: AnalysisOut
