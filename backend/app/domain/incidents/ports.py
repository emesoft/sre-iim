"""Ports (interfaces) the incident use cases depend on. Implemented in the infrastructure layer.

Defining these here inverts the dependency: the application layer talks to abstractions owned by the
domain, and infrastructure adapters (SQLAlchemy, Bedrock, system clock) implement them. Inner layers
never import concrete clients.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from app.domain.incidents.entities import (
    Analysis,
    AnalysisDraft,
    ChatMessage,
    ChatSession,
    Incident,
    IncidentRollup,
    LogEvent,
    UsageByModel,
)
from app.domain.shared import Clock, UnitOfWork  # re-exported for existing imports

if TYPE_CHECKING:
    from app.domain.documents.entities import RetrievedChunk

__all__ = [
    "Analyzer",
    "AnalyzerSelector",
    "ContextEnricher",
    "FixedAnalyzer",
    "IncidentRepository",
    "AnalysisCacheRepository",
    "LogFetcher",
    "TicketClient",
    "Clock",
    "UnitOfWork",
    "ProgressReporter",
    "NullReporter",
    "ChatRepository",
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
        lane: str | None = None,
        projects: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Incident, Analysis | None]]:
        """`projects` is the caller's access scope: `None` means unrestricted (admin), and an
        empty sequence means no access — never "no filter". See domain/users/scope.py."""
        ...

    async def list_pending_auto_analysis(
        self, *, priorities: Sequence[str], limit: int
    ) -> list[Incident]:
        """Incidents that were never analyzed and whose alarm reported one of `priorities` (see
        `context["priority"]`, set by PollAlarmsJob). Newest first, capped at `limit`."""
        ...

    async def rollup(
        self,
        *,
        since: datetime,
        noisy_limit: int = 5,
        projects: Sequence[str] | None = None,
        service: str | None = None,
    ) -> IncidentRollup:
        """Dashboard aggregates, computed in SQL. `since` bounds the "recently noisy" grouping and
        the 24h intake count; it does not bound the open-incident counts, which are about the
        current backlog whatever its age."""
        ...

    async def list_by_date_range(
        self,
        start: datetime,
        end: datetime,
        *,
        service: str | None = None,
        projects: Sequence[str] | None = None,
    ) -> list[tuple[Incident, Analysis | None]]:
        """Incidents created in `[start, end)`, newest first — backs the daily report.
        `service` optionally scopes the report to one project."""
        ...

    async def add_analysis(self, analysis: Analysis) -> Analysis: ...

    async def latest_analysis(self, incident_id: uuid.UUID) -> Analysis | None: ...

    async def usage_by_model(self) -> list[UsageByModel]:
        """Real LLM token spend grouped by `model_id` — cache HITs and untracked providers
        (input_tokens IS NULL) are excluded. Backs the Settings-page usage summary."""
        ...

    async def reset_stale_analyzing(self, cutoff: datetime) -> int:
        """Return incidents stuck in "analyzing" since before `cutoff` to "new", and say how many.

        Analysis runs as an in-process background task, so a restart mid-run leaves the row claiming
        to be in progress with nothing progressing it — and the error path that would have marked it
        "failed" never gets to run either. Nothing else notices, so the incident sits there looking
        busy forever.
        """
        ...

    async def set_status(
        self, incident_id: uuid.UUID, status: str, *, error_message: str | None = None
    ) -> None:
        """`error_message` is stored as-is (only meaningful for `status="failed"`) and cleared
        (set to None) on every other status — a new attempt or a success must not leave a stale
        failure reason from a previous try."""
        ...

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

    async def delete(self, incident_id: uuid.UUID) -> None:
        """Permanently remove the incident and its analyses, clearing any dangling references
        from other tables first (see the SQLAlchemy implementation for the exact list)."""
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

    async def create_ticket(
        self, title: str, description: str, *, related_url: str | None = None
    ) -> str:
        """`related_url` links the new ticket back to another one (e.g. a recurrence of a known
        issue) when the tracker supports it — implementations that don't may ignore it."""
        ...


class ContextEnricher(Protocol):
    """Gathers live evidence about an incident before it is analysed.

    An alarm notification carries almost nothing; the facts that decide the diagnosis — the real
    metric values, whether the service has any running tasks — live in the provider's API. Without
    this the analyzer can only hypothesise and hand a human a checklist, which is a summary rather
    than an investigation.

    Returns extra context keys to merge in. It must never raise: evidence is a bonus, and an
    analysis with less of it beats no analysis at all.
    """

    async def enrich(self, service: str | None, context: dict) -> dict: ...


class AnalyzerSelector(Protocol):
    """Which analyzer should handle a given project's incidents.

    A seam rather than a plain `Analyzer` because the model is configurable per project: the
    choice can only be made once the incident's own project is known, which is inside the use
    case, not at the point its dependencies are wired.
    """

    async def for_project(self, project: str | None) -> Analyzer: ...


@dataclass
class FixedAnalyzer:
    """The selector for "one analyzer, whatever the project" — every test, and any caller that
    has already resolved one."""

    analyzer: Analyzer

    async def for_project(self, project: str | None) -> Analyzer:
        return self.analyzer


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


class ChatRepository(Protocol):
    """Persistence for the per-incident chat transcript and Claude Code session continuity."""

    async def get_or_create_session(self, incident_id: uuid.UUID) -> ChatSession:
        """Returns the existing session for this incident, or creates one with a fresh
        `claude_session_id` if it has never been chatted with."""
        ...

    async def replace_session_id(self, incident_id: uuid.UUID, claude_session_id: uuid.UUID) -> None:
        """Overwrite the stored session id — used when `--resume` fails (e.g. the backend
        container was recreated between turns) and a fresh session was started instead."""
        ...

    async def clear(self, incident_id: uuid.UUID) -> None:
        """Forget this incident's conversation and its provider session — see the implementation
        for why a session can outlive its usefulness."""
        ...

    async def add_message(self, message: ChatMessage) -> ChatMessage: ...

    async def list_messages(self, incident_id: uuid.UUID) -> list[ChatMessage]:
        """Oldest first — the order the frontend renders them in."""
        ...
