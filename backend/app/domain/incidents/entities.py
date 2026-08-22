"""Incident domain entities and value objects — plain dataclasses, no ORM/framework coupling.

Repositories in the infrastructure layer map these to/from persistence rows; the interface layer
maps them to/from DTOs. Inner layers work with these types, not raw dicts or ORM rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Incident:
    """One ingested incident and its raw context."""

    service: str
    source: str  # auto | manual | webhook
    fingerprint: str
    context: dict
    status: str = "new"  # new | analyzing | analyzed | failed | ticketed | resolved
    log_group: str | None = None
    ticket_url: str | None = None
    # The reason the last analysis attempt failed (status == "failed"). Cleared whenever a new
    # attempt starts or succeeds — see IncidentRepository.set_status.
    error_message: str | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class LogEvent:
    """One log line fetched from a `LogFetcher` (e.g. CloudWatch Logs Insights).

    `level` is the line's severity when it can be determined. The analysis prompt renders
    `{ts} {level} {message}` per line, so a missing level is what the model reads — hence it is
    carried here rather than left buried inside `message`.
    """

    timestamp: datetime
    message: str
    level: str | None = None


@dataclass(frozen=True)
class AnalysisDraft:
    """Raw 5-field result produced by an Analyzer, before normalization/persistence.

    `confidence` is the analyzer's own value — the Step 0 qualitative label (`high`/`medium`/`low`)
    or, from a future numeric analyzer, a number. The application layer normalizes it via
    `confidence_to_score` when building the persisted `Analysis`.

    `evidence_chunk_ids` are the retrieved chunks the analyzer grounded on (empty for a plain
    single-call analyzer; populated by the RAG / graph analyzers that retrieve internally).
    """

    severity: str
    summary: str
    root_cause: str
    recommended_action: str
    confidence: object
    model_id: str
    evidence_chunk_ids: tuple[uuid.UUID, ...] = ()
    # Set by RagAnalyzer when a retrieved chunk is a past resolved incident above the
    # known-issue similarity threshold — "we've seen this before, here's how it was fixed".
    known_issue_incident_id: uuid.UUID | None = None
    known_issue_similarity: float | None = None
    # Only populated by providers that report usage (currently claude_cli). None means "not
    # tracked for this provider", not "zero tokens used" — do not treat as 0 in aggregates.
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class Analysis:
    """A persisted analysis of an incident."""

    incident_id: uuid.UUID
    severity: str
    summary: str
    root_cause: str
    recommended_action: str
    confidence: float | None
    cache_state: str  # HIT | MISS
    model_id: str
    evidence_chunk_ids: list[uuid.UUID] = field(default_factory=list)
    known_issue_incident_id: uuid.UUID | None = None
    known_issue_similarity: float | None = None
    # See AnalysisDraft — None means untracked for this provider; a cache HIT is explicitly 0
    # (no LLM call was made), never a copy of the original MISS's token count.
    input_tokens: int | None = None
    output_tokens: int | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class UsageByModel:
    """Total tracked LLM token usage for one `model_id`, real-spend only: cache HITs (no LLM call)
    and providers that don't report usage (input_tokens IS NULL) are excluded, not zeroed in."""

    model_id: str
    input_tokens: int
    output_tokens: int
    analyses_count: int
