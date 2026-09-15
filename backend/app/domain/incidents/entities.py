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
    # How many times the same CloudWatch alarm (by ARN) has fired into a new incident, counting
    # this one — 1 for a first-time alarm or any non-alarm-sourced incident. Set once at creation
    # by PollAlarmsJob; never updated afterward. `previous_incident_id` links back to the incident
    # this alarm opened last time it fired (None on the first occurrence).
    occurrence_count: int = 1
    previous_incident_id: uuid.UUID | None = None
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
    #: Stamped by the selector that chose the provider — adapters don't know profiles exist.
    llm_profile: str | None = None
    evidence_chunk_ids: tuple[uuid.UUID, ...] = ()
    # Set by RagAnalyzer when a retrieved chunk is a past resolved incident above the
    # known-issue similarity threshold — "we've seen this before, here's how it was fixed".
    known_issue_incident_id: uuid.UUID | None = None
    known_issue_similarity: float | None = None
    # Only populated by providers that report usage (currently claude_cli). None means "not
    # tracked for this provider", not "zero tokens used" — do not treat as 0 in aggregates.
    # `input_tokens` is fresh input only; the cached prefix is counted apart because it costs a
    # fraction of the price, and adding them together misreports spend as badly as omitting it.
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
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
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    #: The model profile that ran it, for cost attribution. None for analyses that predate
    #: profiles, and for anything running straight off the environment.
    llm_profile: str | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class UsageByModel:
    """Total tracked LLM token usage for one (profile, model), real-spend only: cache HITs (no LLM
    call) and providers that don't report usage (input_tokens IS NULL) are excluded, not zeroed in.

    Grouped by profile as well as model because two cohorts billing to two different keys can be
    running the same model — one line for both would hide exactly the split this is here to show.
    """

    model_id: str
    input_tokens: int
    output_tokens: int
    analyses_count: int
    #: Cache reads and writes, kept apart from `input_tokens`: they cost a fraction of the price,
    #: and the same cached prefix is re-sent on every call. Folded in, the total reads as roughly
    #: an order of magnitude more spend than actually happened.
    cached_input_tokens: int = 0
    #: Input tokens from rows recorded before the split existed, where the figure includes the
    #: cached prefix and cannot be separated now. Kept out of `input_tokens` rather than folded in:
    #: calling a combined number "fresh input" repeats exactly the overstatement the split fixed.
    unsplit_input_tokens: int = 0
    llm_profile: str | None = None
    #: "analysis" | "chat". Both land in this table but a missing profile means different things
    #: in each: an analysis predates profiles existing, a chat message never records one. Without
    #: this the UI can only tell them apart by matching on the model id string.
    source: str = "analysis"


@dataclass(frozen=True)
class ProjectRollup:
    """One project's incident posture, aggregated in the database.

    `top_context` is the raw context of the incident that most deserves attention in this project
    (most severe first, newest as the tiebreak) — the interface layer turns it into a headline
    with the same `build_headline` every incident list uses, rather than a second wording of the
    same rule here."""

    project: str
    open: int
    urgent: int
    untriaged: int
    top_incident_id: uuid.UUID | None = None
    top_context: dict | None = None


@dataclass(frozen=True)
class NoisyAlarm:
    """One repeatedly-firing problem inside the rollup window, grouped by fingerprint — the same
    alarm re-firing shares a fingerprint, so this is "what is spamming us", not "what fired last"."""

    service: str
    fingerprint: str
    count: int
    context: dict | None = None


@dataclass(frozen=True)
class IncidentRollup:
    """Dashboard totals computed in SQL rather than from a page of incidents.

    The dashboard used to derive its numbers from `GET /api/incidents`, which is capped at 50 rows
    — so every count silently became a lie past that point, and per-project figures could never be
    right at all. Everything the dashboard shows now comes from this one aggregate."""

    active: int
    urgent: int
    untriaged: int
    new_last_24h: int
    projects: tuple[ProjectRollup, ...] = ()
    noisy: tuple[NoisyAlarm, ...] = ()
    #: How many incidents sit in each triage lane (see LANES). Computed here rather than counted
    #: from a page of rows, for the same reason as every other number on this object.
    lanes: dict[str, int] = field(default_factory=dict)
    #: Every project the caller can see incidents for, regardless of the `service` filter — the
    #: options for the filter itself, which would otherwise collapse to the one already chosen.
    all_projects: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatMessage:
    """One turn in an incident's chat transcript (design spec 2026-08-23). Append-only — never
    mutated after creation."""

    incident_id: uuid.UUID
    role: str  # user | assistant
    content: str
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class ChatSession:
    """Ties one incident to the Claude Code CLI session id used for `--resume`, so a multi-turn
    chat doesn't need to resend the full transcript on every message."""

    incident_id: uuid.UUID
    claude_session_id: uuid.UUID
    created_at: datetime | None = None


@dataclass(frozen=True)
class ChatTurnResult:
    """One chat turn's outcome from an `IncidentChatProvider`. `claude_session_id` is the session
    actually used — it can differ from the one requested if the provider had to start a fresh
    session (e.g. a stale `--resume` target after the backend container was recreated)."""

    text: str
    #: Fresh input only. Cached prefix (system prompt, incident context, transcript) is counted
    #: separately because it costs a fraction of the price and is re-read on every turn — summing
    #: the two overstates spend by about an order of magnitude.
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    claude_session_id: uuid.UUID
