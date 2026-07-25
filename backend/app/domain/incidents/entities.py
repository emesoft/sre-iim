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
    id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class LogEvent:
    """One log line fetched from a `LogFetcher` (e.g. CloudWatch Logs Insights)."""

    timestamp: datetime
    message: str


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
    id: uuid.UUID | None = None
    created_at: datetime | None = None
