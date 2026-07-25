"""Ports (interfaces) the incident use cases depend on. Implemented in the infrastructure layer.

Defining these here inverts the dependency: the application layer talks to abstractions owned by the
domain, and infrastructure adapters (SQLAlchemy, Bedrock, system clock) implement them. Inner layers
never import concrete clients.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from app.domain.incidents.entities import Analysis, AnalysisDraft, Incident, LogEvent
from app.domain.shared import Clock, UnitOfWork  # re-exported for existing imports

if TYPE_CHECKING:
    from app.domain.documents.entities import RetrievedChunk

__all__ = [
    "Analyzer",
    "IncidentRepository",
    "AnalysisCacheRepository",
    "LogFetcher",
    "TicketClient",
    "Clock",
    "UnitOfWork",
    "ProgressReporter",
    "NullReporter",
]


class Analyzer(Protocol):
    """Produces an analysis draft for an incident context (M2: one LLM call; M3: the agent graph).

    `evidence` (retrieved knowledge chunks) is optional; when supplied, the analyzer grounds its
    reasoning and cites it. M2/no-RAG callers pass nothing and behave as before.

    `reporter` is optional; multi-step analyzers (RagAnalyzer, GraphAnalyzer) report their stages
    through it for live progress (SSE streaming design, decision 2026-07-20). Single-call base
    analyzers (Bedrock/DeepSeek) have no internal stages to report and ignore it.
    """

    async def analyze(
        self,
        context: dict,
        evidence: "list[RetrievedChunk] | None" = None,
        reporter: "ProgressReporter | None" = None,
    ) -> AnalysisDraft: ...


class IncidentRepository(Protocol):
    """Persistence for incidents and their analyses."""

    async def add(self, incident: Incident) -> Incident: ...

    async def get(self, incident_id: uuid.UUID) -> Incident | None: ...

    async def list(
        self,
        *,
        service: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Incident, Analysis | None]]: ...

    async def list_by_date_range(
        self, start: datetime, end: datetime
    ) -> list[tuple[Incident, Analysis | None]]:
        """Incidents created in `[start, end)`, newest first — backs the daily report."""
        ...

    async def add_analysis(self, analysis: Analysis) -> Analysis: ...

    async def latest_analysis(self, incident_id: uuid.UUID) -> Analysis | None: ...

    async def set_status(self, incident_id: uuid.UUID, status: str) -> None: ...

    async def update_context(
        self,
        incident_id: uuid.UUID,
        *,
        context: dict,
        fingerprint: str,
        log_group: str | None = None,
    ) -> None:
        """Replace an incident's context/fingerprint (e.g. after merging fetched log lines) so a
        follow-up analysis re-runs against the new content instead of hitting the stale cache."""
        ...

    async def set_ticket_url(self, incident_id: uuid.UUID, ticket_url: str) -> None:
        """Record a created tracking ticket and move the incident to status 'ticketed'."""
        ...


class AnalysisCacheRepository(Protocol):
    """Fingerprint-keyed cache mapping to a previously computed analysis, honoring a TTL."""

    async def get_valid(self, fingerprint: str, now: datetime) -> Analysis | None: ...

    async def put(self, fingerprint: str, analysis_id: uuid.UUID, expires_at: datetime) -> None: ...


class LogFetcher(Protocol):
    """Fetches recent log lines for a log group (e.g. CloudWatch Logs Insights). Used by the
    on-demand log-search action on an incident — not part of the ingest/analyze flow."""

    async def fetch_logs(
        self,
        log_group: str,
        start: datetime,
        end: datetime,
        filter_pattern: str | None = None,
    ) -> list[LogEvent]: ...


class TicketClient(Protocol):
    """Creates a tracking ticket for an incident in an external tracker (e.g. Azure DevOps).
    Returns the created ticket's URL."""

    async def create_ticket(self, title: str, description: str) -> str: ...


class ProgressReporter(Protocol):
    """Notified of analysis progress (SSE streaming design, decision 2026-07-20).

    Analyzers call `stage()` as they enter each step, so a caller (the ingest use case, an SSE
    endpoint) can surface live progress. Purely observational — never raises, never influences
    the analysis itself.
    """

    async def stage(self, name: str, detail: str | None = None) -> None: ...


class NullReporter:
    """Default no-op ProgressReporter — existing callers (tests, the dev/debug harness) that
    don't pass one keep working unchanged."""

    async def stage(self, name: str, detail: str | None = None) -> None:
        return None
