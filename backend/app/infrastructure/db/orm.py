"""SQLAlchemy ORM tables (persistence models) for all domains (SPEC section 8).

These are infrastructure detail, kept separate from the domain entities; repositories map between
the two. Importing this module registers every table on `Base.metadata` for Alembic autogenerate.

SQLAlchemy 2.0 typed ORM: https://docs.sqlalchemy.org/en/20/orm/declarative_styles.html
pgvector SQLAlchemy: https://github.com/pgvector/pgvector-python
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.infrastructure.config import get_settings

# Embedding dimension is provider-specific (Titan 1024 / Jina 768) and configurable (decision 0016).
# A pgvector column has a fixed dimension, so one database serves one embedding provider at a time.
EMBED_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    pass


def _utcnow_column(*, on_update: bool = False) -> Mapped[datetime]:
    """A timezone-aware timestamp defaulting to now() at the database.

    `on_update=True` also re-stamps it on every UPDATE — which is what a column called `updated_at`
    has to do to be worth reading. Without it the value silently means "created_at", and anything
    reasoning about staleness from it is wrong by exactly the age of the row.
    """
    kwargs = {"onupdate": func.now()} if on_update else {}
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, **kwargs
    )


class IncidentRow(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # auto | manual | webhook
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="new")
    log_group: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    previous_incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True
    )
    created_at: Mapped[datetime] = _utcnow_column()
    updated_at: Mapped[datetime] = _utcnow_column(on_update=True)


class AnalysisRow(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False, index=True
    )
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    cache_state: Mapped[str] = mapped_column(Text, nullable=False)  # HIT | MISS
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Which model profile ran it (migration 0026) — the model id alone can't tell two teams'
    # keys apart when both are on the same model.
    llm_profile: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    evidence_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    known_issue_incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True
    )
    known_issue_similarity: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Cache reads + writes (migration 0027). Apart from `input_tokens` because they are priced
    # differently and re-sent every turn; NULL on rows recorded before the split existed.
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _utcnow_column()


class AnalysisCacheRow(Base):
    __tablename__ = "analysis_cache"

    fingerprint: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    # runbook | postmortem | architecture | vendor | incident
    service: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True
    )
    created_at: Mapped[datetime] = _utcnow_column()
    updated_at: Mapped[datetime] = _utcnow_column(on_update=True)


class DocChunkRow(Base):
    __tablename__ = "doc_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    service: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM), nullable=False)


class UserRow(Base):
    """A per-user account (username + password) with a role: admin | sre | consultant. Was an
    orphaned OAuth-shaped table (email/name/provider, no password/role) until per-user login was
    built — see `.claude/plans` for the auth/RBAC design. `username` is the unique login identity;
    `email` is optional, purely informational (not unique, not used for login)."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("auth_provider", "external_id", name="uq_users_provider_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL for an account that authenticates elsewhere (Entra) — a null hash can never verify,
    # so those accounts are structurally unreachable through the password login path.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_provider: Mapped[str] = mapped_column(
        Text, nullable=False, default="local", server_default="local"
    )
    # Entra's `oid`. Usernames and email addresses change; this doesn't.
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which group this account belongs to. NULL is the Guest state: no permissions, no projects.
    # ON DELETE SET NULL, so deleting a group revokes its members' access rather than orphaning it.
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="SET NULL"), nullable=True
    )
    # Eager so `session.get(UserRow, id)` — the auth path on every request — resolves the role and
    # projects without a second query or a lazy load on an async session.
    group: Mapped["GroupRow | None"] = relationship(lazy="joined")
    created_at: Mapped[datetime] = _utcnow_column()


class GroupRow(Base):
    """A group: a permission level plus the projects it opens up (migration 0025). Replaced the
    `users.role` column and the teams tables, which together made the admin screen read as two
    systems answering the same question."""

    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # An optional model profile this cohort's analyses bill to (migration 0026). NULL follows the
    # deployment default, which is what almost every group should do.
    model_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    projects: Mapped[list["GroupProjectRow"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan"
    )
    created_at: Mapped[datetime] = _utcnow_column()


class GroupProjectRow(Base):
    __tablename__ = "group_projects"

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    project: Mapped[str] = mapped_column(
        Text,
        ForeignKey("projects.name", onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class ProjectRow(Base):
    """The shared registry of project names — see `.claude/specs/2026-08-23-project-registry-design.md`."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Per-project switch for automatic triage (migration 0020) — the global AUTO_ANALYZE_* env
    # settings are all-or-nothing, and one noisy project shouldn't force everyone off.
    auto_analyze: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = _utcnow_column()


class IntegrationRow(Base):
    """One provider hooked up to one project (migration 0019).

    Everything provider-specific lives in `config` (non-secret) and `encrypted_secrets` (each
    value encrypted individually, keys are plain names) rather than in dedicated columns — the
    previous table was shaped for AWS and had New Relic pushed through the same columns, storing
    a New Relic account ID in a column called `region`.

    `capabilities` is why one row can serve several jobs: an AWS credential polls alarms, fetches
    logs and (later) reads cost, while a New Relic one only does alarms.
    """

    __tablename__ = "integrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.name", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    env: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)  # aws | newrelic | azure_devops
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    encrypted_secrets: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    created_at: Mapped[datetime] = _utcnow_column()


class IntegrationHealthRow(Base):
    """Last run of one capability of one integration. Per-capability because they fail
    independently — a broken cost sync says nothing about whether alarm polling still works."""

    __tablename__ = "integration_health"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), primary_key=True
    )
    capability: Mapped[str] = mapped_column(Text, primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)  # ok | error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TrackedAlarmRow(Base):
    __tablename__ = "tracked_alarms"
    __table_args__ = (
        UniqueConstraint("connection_id", "alarm_arn", name="uq_tracked_alarms_connection_arn"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alarm_arn: Mapped[str] = mapped_column(Text, nullable=False)
    alarm_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_state: Mapped[str] = mapped_column(Text, nullable=False)  # OK | ALARM
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True
    )
    last_incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = _utcnow_column(on_update=True)


class AppSettingRow(Base):
    """Generic encrypted key-value store for app-wide secrets (e.g. the Claude Code headless
    OAuth token entered on the Settings page) that don't fit the per-project cloud_connections
    shape."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChatSessionRow(Base):
    __tablename__ = "chat_sessions"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    claude_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = _utcnow_column()


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Monotonic insert order, distinct from `id`/PK — a chat turn's user+assistant rows share one
    # transaction so `created_at` (transaction start time) ties; this is the tie-break for ordering.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=False), nullable=False)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Cache reads + writes (migration 0027). Apart from `input_tokens` because they are priced
    # differently and re-sent every turn; NULL on rows recorded before the split existed.
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _utcnow_column()


class DailyReportRow(Base):
    """One generated daily digest, keyed by (report_date, service) so it's only ever computed
    once per day per project scope — regenerating (the UI's "Regenerate report") overwrites the
    same row rather than accumulating history. `service = ''` is the "All projects" scope; a real
    project name is never empty, so this sentinel can't collide with one (kept as a plain string
    column rather than nullable — NULL isn't equal to NULL in a unique constraint, which would let
    "All projects" for one date be saved more than once)."""

    __tablename__ = "daily_reports"
    __table_args__ = (
        UniqueConstraint("report_date", "service", name="uq_daily_reports_date_service"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    service: Mapped[str] = mapped_column(Text, nullable=False, default="")
    counts_by_severity: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    counts_by_status: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    incidents: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    slack_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _utcnow_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
