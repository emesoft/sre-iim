# Incident Chat (Claude, tool-calling) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the incident detail page's "Search logs" panel with a chat panel where the user
asks Claude free-form questions about one incident, and Claude autonomously calls a `fetch_logs`
tool (via its own MCP tool-use loop) when it needs log lines it doesn't already have.

**Architecture:** A new MCP stdio server (`mcp_log_tool.py`, spawned per chat turn by `claude -p
--mcp-config`) exposes `fetch_logs`, reusing the existing `LogFetcher`/`build_log_fetcher` code
directly (same process image, no network hop). `ClaudeCliChat` (new, in `claude_cli.py`) drives one
chat turn with `--session-id`/`--resume` for multi-turn continuity. A new `IncidentChat` use case
persists both sides of every turn to two new tables (`chat_sessions`, `chat_messages`) and exposes
`GET`/`POST /api/incidents/{id}/chat`. The old log-search endpoint/DTOs/panel are deleted.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic (backend), Vite + React + TypeScript
(frontend), `claude` CLI headless mode (`app/infrastructure/llm/claude_cli.py`), new dependency:
`mcp` (official Python MCP SDK).

**Spec:** `.claude/specs/2026-08-23-incident-chat-design.md` — read it before starting; this plan
argues from it and does not repeat its rationale.

## Global Constraints

- **claude_cli only.** `POST /api/incidents/{id}/chat` returns `501` when
  `settings.llm_provider != "claude_cli"` — this feature depends on the real Claude Code CLI's own
  MCP tool-use loop, which only exists for that provider.
- **`fetch_logs` is scoped by construction, not by model-controlled input.** The incident's
  `service` is baked into the MCP server subprocess's env at launch time
  (`IIM_INCIDENT_SERVICE`) — it is never a tool parameter Claude can set, so a chat about one
  project's incident cannot be steered into fetching another project's logs.
- **`--strict-mcp-config` and an explicit `--allowedTools` allowlist always accompany the chat
  call** — no project/user-level MCP config leaks in, and no tool other than `fetch_logs` is ever
  available during a chat turn.
- **`input_tokens`/`output_tokens` on a chat message follow the same convention as `analyses`**:
  `None` means untracked, never 0; both are set together or both left `None`.
- Every backend test in this plan runs against the `iim_test` Postgres database via
  `TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test"` — never the
  real `iim` database (see this repo's established practice from prior sessions). After each
  migration task, also run `ALTER TABLE ...` by hand against `iim_test` (Postgres does not
  auto-apply new columns/tables to an already-`create_all`'d test database — see Task 2, Step 6).
- Run `uv run ruff check app/ tests/` (backend) and `npm run build` (frontend) at the end of every
  task that touches that side — both must be clean before committing.

---

### Task 1: Domain layer — `ChatMessage`, `ChatSession`, `ChatTurnResult` entities, `ChatRepository` and `IncidentChatProvider` ports

**Files:**
- Modify: `backend/app/domain/incidents/entities.py`
- Modify: `backend/app/domain/incidents/ports.py`
- Modify: `backend/app/domain/llm.py`
- Test: `backend/tests/test_chat_entities.py` (new)

**Interfaces:**
- Produces: `ChatMessage(incident_id, role, content, input_tokens=None, output_tokens=None, id=None, created_at=None)`, `ChatSession(incident_id, claude_session_id, created_at=None)`, `ChatTurnResult(text, input_tokens, output_tokens, claude_session_id)` — all frozen dataclasses in `app.domain.incidents.entities`. `ChatRepository` Protocol in `app.domain.incidents.ports` with `get_or_create_session`, `replace_session_id`, `add_message`, `list_messages`. `IncidentChatProvider` Protocol in `app.domain.llm` with one method `send(...)`.
- Consumes: nothing new (pure additions).

- [ ] **Step 1: Add the three dataclasses to `entities.py`**

Append to the end of `backend/app/domain/incidents/entities.py` (after the existing `UsageByModel` class):

```python
@dataclass(frozen=True)
class ChatMessage:
    """One turn in an incident's chat transcript (design spec 2026-08-23). Append-only — never
    mutated after creation."""

    incident_id: uuid.UUID
    role: str  # user | assistant
    content: str
    input_tokens: int | None = None
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
    input_tokens: int | None
    output_tokens: int | None
    claude_session_id: uuid.UUID
```

- [ ] **Step 2: Add `ChatRepository` to `ports.py`**

In `backend/app/domain/incidents/ports.py`, change the import line:

```python
from app.domain.incidents.entities import Analysis, AnalysisDraft, Incident, LogEvent, UsageByModel
```

to:

```python
from app.domain.incidents.entities import (
    Analysis,
    AnalysisDraft,
    ChatMessage,
    ChatSession,
    Incident,
    LogEvent,
    UsageByModel,
)
```

Add `"ChatRepository"` to the `__all__` list (currently ends `"NullReporter",`):

```python
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
    "ChatRepository",
]
```

Append this class at the end of the file (after `NullReporter`):

```python
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

    async def add_message(self, message: ChatMessage) -> ChatMessage: ...

    async def list_messages(self, incident_id: uuid.UUID) -> list[ChatMessage]:
        """Oldest first — the order the frontend renders them in."""
        ...
```

- [ ] **Step 3: Add `IncidentChatProvider` to `domain/llm.py`**

Read `backend/app/domain/llm.py` first to confirm its current full content (it should be the
`ChatModel` Protocol + `Tier` literal only). Replace the whole file with:

```python
"""Generic LLM chat ports (decision 0011 for ChatModel; design spec 2026-08-23 for
IncidentChatProvider).

`ChatModel` is used by the multi-agent graph nodes and the daily report — free-form completions,
no tools. `IncidentChatProvider` is used by the incident chat feature — one turn, with real
tool-calling (the provider's own agentic loop decides whether to call a tool). Only `claude_cli`
implements `IncidentChatProvider`; other providers are rejected with a 501 at the HTTP layer
before this port is ever called (see `interface/http/incidents.py`).
"""

from __future__ import annotations

import uuid
from typing import Literal, Protocol

from app.domain.incidents.entities import ChatTurnResult

Tier = Literal["fast", "main"]


class ChatModel(Protocol):
    async def complete(self, system: str, user: str, *, tier: Tier = "main") -> str: ...


class IncidentChatProvider(Protocol):
    """One incident-chat turn, with real tool-calling — see module docstring."""

    async def send(
        self,
        *,
        service: str,
        context: dict,
        message: str,
        claude_session_id: uuid.UUID,
        is_new_session: bool,
    ) -> ChatTurnResult: ...
```

- [ ] **Step 4: Write a smoke test**

Create `backend/tests/test_chat_entities.py`:

```python
"""Smoke test for the new chat domain entities — confirms defaults and that the ports module
still imports cleanly with ChatRepository added."""

import uuid

from app.domain.incidents.entities import ChatMessage, ChatSession, ChatTurnResult
from app.domain.incidents.ports import ChatRepository
from app.domain.llm import IncidentChatProvider


def test_chat_message_defaults():
    incident_id = uuid.uuid4()
    msg = ChatMessage(incident_id=incident_id, role="user", content="hello")
    assert msg.input_tokens is None
    assert msg.output_tokens is None
    assert msg.id is None


def test_chat_session_holds_ids():
    incident_id = uuid.uuid4()
    session_id = uuid.uuid4()
    session = ChatSession(incident_id=incident_id, claude_session_id=session_id)
    assert session.claude_session_id == session_id


def test_chat_turn_result_fields():
    session_id = uuid.uuid4()
    result = ChatTurnResult(text="hi", input_tokens=10, output_tokens=5, claude_session_id=session_id)
    assert result.text == "hi"
    assert result.claude_session_id == session_id


def test_ports_are_protocols():
    # No instantiation — Protocols aren't meant to be constructed. This just confirms the module
    # still imports (ChatRepository added to ports.py, IncidentChatProvider added to llm.py).
    assert ChatRepository is not None
    assert IncidentChatProvider is not None
```

- [ ] **Step 5: Run the test**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest tests/test_chat_entities.py -v`
Expected: 4 passed.

- [ ] **Step 6: Run the full suite + lint to confirm nothing else broke**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest -q && uv run ruff check app/ tests/`
Expected: same pass/fail counts as before this task (5 pre-existing pgvector-dimension failures unrelated to this work are expected and not a regression), ruff clean.

- [ ] **Step 7: Commit**

```bash
cd /home/hieuly/hieuly/project/LLM-SRE
git add backend/app/domain/incidents/entities.py backend/app/domain/incidents/ports.py backend/app/domain/llm.py backend/tests/test_chat_entities.py
git commit -m "feat: add chat domain entities and ports for incident chat"
```

---

### Task 2: Persistence — migration, ORM rows, `SqlAlchemyChatRepository`, mappers

**Files:**
- Create: `backend/migrations/versions/0011_chat.py`
- Modify: `backend/app/infrastructure/db/orm.py`
- Modify: `backend/app/infrastructure/db/repositories/mappers.py`
- Create: `backend/app/infrastructure/db/repositories/chat.py`
- Modify: `backend/app/infrastructure/db/repositories/__init__.py`
- Test: `backend/tests/test_chat_repository.py` (new)

**Interfaces:**
- Consumes: `ChatMessage`, `ChatSession` from Task 1.
- Produces: `SqlAlchemyChatRepository(session)` implementing `ChatRepository` exactly (same method names/signatures as Task 1 Step 2); `chat_message_to_domain(row) -> ChatMessage`, `chat_session_to_domain(row) -> ChatSession` in `mappers.py`; ORM classes `ChatSessionRow`, `ChatMessageRow` in `orm.py`.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/versions/0011_chat.py`:

```python
"""add chat_sessions and chat_messages

Revision ID: 0011_chat
Revises: 0010_incident_error_message
Create Date: 2026-08-23

Persists the incident chat feature's session continuity (chat_sessions.claude_session_id, used
with `claude -p --session-id`/`--resume` so a multi-turn conversation doesn't need to resend full
history) and the displayed transcript (chat_messages). See design spec
.claude/specs/2026-08-23-incident-chat-design.md.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_chat"
down_revision: Union[str, None] = "0010_incident_error_message"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id"),
            primary_key=True,
        ),
        sa.Column("claude_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id"), nullable=False
        ),
        sa.Column("role", sa.Text(), nullable=False),  # user | assistant
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_chat_messages_incident_id", "chat_messages", ["incident_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_incident_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
```

- [ ] **Step 2: Add the ORM rows**

In `backend/app/infrastructure/db/orm.py`, append at the end of the file (after `AppSettingRow`):

```python
class ChatSessionRow(Base):
    __tablename__ = "chat_sessions"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), primary_key=True
    )
    claude_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = _utcnow_column()


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _utcnow_column()
```

- [ ] **Step 3: Add the mapper functions**

In `backend/app/infrastructure/db/repositories/mappers.py`, change the imports at the top:

```python
from app.domain.cloud_connections.entities import CloudConnection, TrackedAlarm
from app.domain.documents.entities import Document
from app.domain.incidents.entities import Analysis, ChatMessage, ChatSession, Incident
from app.infrastructure.db.orm import (
    AnalysisRow,
    ChatMessageRow,
    ChatSessionRow,
    DocumentRow,
    IncidentRow,
)
```

Append these two functions at the end of the file:

```python
def chat_message_to_domain(row: ChatMessageRow) -> ChatMessage:
    return ChatMessage(
        incident_id=row.incident_id,
        role=row.role,
        content=row.content,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        id=row.id,
        created_at=row.created_at,
    )


def chat_session_to_domain(row: ChatSessionRow) -> ChatSession:
    return ChatSession(
        incident_id=row.incident_id,
        claude_session_id=row.claude_session_id,
        created_at=row.created_at,
    )
```

- [ ] **Step 4: Write `SqlAlchemyChatRepository`**

Create `backend/app/infrastructure/db/repositories/chat.py`:

```python
"""SQLAlchemy repository for the incident chat transcript and Claude Code session continuity
(implements ChatRepository, domain/incidents/ports.py)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.incidents.entities import ChatMessage, ChatSession
from app.infrastructure.db.orm import ChatMessageRow, ChatSessionRow
from app.infrastructure.db.repositories.mappers import (
    chat_message_to_domain,
    chat_session_to_domain,
)


class SqlAlchemyChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_or_create_session(self, incident_id: uuid.UUID) -> ChatSession:
        row = await self._s.get(ChatSessionRow, incident_id)
        if row is None:
            row = ChatSessionRow(incident_id=incident_id, claude_session_id=uuid.uuid4())
            self._s.add(row)
            await self._s.flush()
            await self._s.refresh(row)
        return chat_session_to_domain(row)

    async def replace_session_id(self, incident_id: uuid.UUID, claude_session_id: uuid.UUID) -> None:
        row = await self._s.get(ChatSessionRow, incident_id)
        if row is not None:
            row.claude_session_id = claude_session_id

    async def add_message(self, message: ChatMessage) -> ChatMessage:
        row = ChatMessageRow(
            incident_id=message.incident_id,
            role=message.role,
            content=message.content,
            input_tokens=message.input_tokens,
            output_tokens=message.output_tokens,
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return chat_message_to_domain(row)

    async def list_messages(self, incident_id: uuid.UUID) -> list[ChatMessage]:
        stmt = (
            select(ChatMessageRow)
            .where(ChatMessageRow.incident_id == incident_id)
            .order_by(ChatMessageRow.created_at.asc())
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [chat_message_to_domain(row) for row in rows]
```

- [ ] **Step 5: Export it**

In `backend/app/infrastructure/db/repositories/__init__.py`, add the import and `__all__` entry:

```python
from app.infrastructure.db.repositories.app_settings import SqlAlchemyAppSettingsRepository
from app.infrastructure.db.repositories.chat import SqlAlchemyChatRepository
from app.infrastructure.db.repositories.cloud_connections import (
    SqlAlchemyCloudConnectionRepository,
    SqlAlchemyTrackedAlarmRepository,
)
from app.infrastructure.db.repositories.documents import (
    SqlAlchemyDocumentRepository,
    SqlAlchemyRetriever,
)
from app.infrastructure.db.repositories.incidents import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyIncidentRepository,
)
from app.infrastructure.db.repositories.unit_of_work import SqlAlchemyUnitOfWork

__all__ = [
    "SqlAlchemyIncidentRepository",
    "SqlAlchemyAnalysisCacheRepository",
    "SqlAlchemyDocumentRepository",
    "SqlAlchemyRetriever",
    "SqlAlchemyUnitOfWork",
    "SqlAlchemyCloudConnectionRepository",
    "SqlAlchemyTrackedAlarmRepository",
    "SqlAlchemyAppSettingsRepository",
    "SqlAlchemyChatRepository",
]
```

- [ ] **Step 6: Apply the new tables to the `iim_test` database**

The test database is not managed by Alembic in this repo's test setup (tests call
`Base.metadata.create_all`, which only creates tables that don't exist yet — it will pick up the
two brand-new tables automatically the first time a test file runs `create_all`). Confirm this
works before writing the repository test:

Run: `PGPASSWORD=change-me psql -h localhost -U iim -d iim_test -c "\dt chat_sessions"`
Expected: `Did not find any relation named "chat_sessions".` (confirms it doesn't exist yet — the
test fixture in Step 8 will create it via `create_all`).

- [ ] **Step 7: Write the failing test**

Create `backend/tests/test_chat_repository.py`:

```python
"""Integration tests for SqlAlchemyChatRepository against real Postgres (iim_test)."""

import os
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.incidents.entities import ChatMessage
from app.infrastructure.db.orm import Base, ChatMessageRow, ChatSessionRow, IncidentRow
from app.infrastructure.db.repositories.chat import SqlAlchemyChatRepository

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)


@pytest.fixture()
async def session():
    engine = create_async_engine(_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"Postgres not reachable: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(ChatMessageRow))
        await s.execute(delete(ChatSessionRow))
        await s.execute(delete(IncidentRow))
        await s.commit()

    async with maker() as s:
        yield s

    await engine.dispose()


async def _make_incident(session) -> uuid.UUID:
    incident_id = uuid.uuid4()
    session.add(
        IncidentRow(
            id=incident_id, service="GCM", source="manual", fingerprint=str(uuid.uuid4()),
            context={}, status="analyzed",
        )
    )
    await session.flush()
    return incident_id


async def test_get_or_create_session_creates_once(session):
    incident_id = await _make_incident(session)
    repo = SqlAlchemyChatRepository(session)

    first = await repo.get_or_create_session(incident_id)
    await session.commit()
    second = await repo.get_or_create_session(incident_id)

    assert first.claude_session_id == second.claude_session_id


async def test_replace_session_id_overwrites_it(session):
    incident_id = await _make_incident(session)
    repo = SqlAlchemyChatRepository(session)
    original = await repo.get_or_create_session(incident_id)
    await session.commit()

    new_id = uuid.uuid4()
    await repo.replace_session_id(incident_id, new_id)
    await session.commit()

    updated = await repo.get_or_create_session(incident_id)
    assert updated.claude_session_id == new_id
    assert updated.claude_session_id != original.claude_session_id


async def test_add_and_list_messages_oldest_first(session):
    incident_id = await _make_incident(session)
    repo = SqlAlchemyChatRepository(session)

    await repo.add_message(ChatMessage(incident_id=incident_id, role="user", content="hi"))
    await repo.add_message(
        ChatMessage(
            incident_id=incident_id, role="assistant", content="hello",
            input_tokens=10, output_tokens=5,
        )
    )
    await session.commit()

    messages = await repo.list_messages(incident_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "hi"
    assert messages[1].input_tokens == 10
    assert messages[1].output_tokens == 5
    assert messages[0].id is not None
    assert messages[0].created_at is not None


async def test_list_messages_empty_for_unchatted_incident(session):
    incident_id = await _make_incident(session)
    repo = SqlAlchemyChatRepository(session)
    assert await repo.list_messages(incident_id) == []
```

- [ ] **Step 8: Run it to verify it fails first (tables don't exist), then passes after `create_all`**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest tests/test_chat_repository.py -v`

The fixture's own `Base.metadata.create_all` call creates the `chat_sessions`/`chat_messages`
tables the first time this test file runs (since Task 2 Step 2 registered the ORM classes on
`Base.metadata` — no manual `ALTER TABLE`/`CREATE TABLE` needed for this specific pair of brand-new
tables, unlike the earlier "add a column to an existing table" cases this repo hit before).

Expected: 4 passed.

- [ ] **Step 9: Run the full suite + lint**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest -q && uv run ruff check app/ tests/`
Expected: same baseline as Task 1 Step 6, plus the 4 new passing tests.

- [ ] **Step 10: Commit**

```bash
cd /home/hieuly/hieuly/project/LLM-SRE
git add backend/migrations/versions/0011_chat.py backend/app/infrastructure/db/orm.py backend/app/infrastructure/db/repositories/mappers.py backend/app/infrastructure/db/repositories/chat.py backend/app/infrastructure/db/repositories/__init__.py backend/tests/test_chat_repository.py
git commit -m "feat: persist incident chat sessions and messages"
```

---

### Task 3: MCP tool server — `fetch_logs`

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/infrastructure/llm/mcp_log_tool.py`
- Test: `backend/tests/test_mcp_log_tool.py` (new)

**Interfaces:**
- Consumes: `build_log_fetcher(service, settings)` (existing, `app.infrastructure.logs.factory`), `get_settings()` (existing, `app.infrastructure.config`).
- Produces: an async function `fetch_logs(log_group: str, start: str, end: str, filter_pattern: str | None = None) -> str`, registered as an MCP tool named `fetch_logs` on a `FastMCP("iim-tools")` server instance named `mcp`, runnable as `python3 -m app.infrastructure.llm.mcp_log_tool` (reads `IIM_INCIDENT_SERVICE` from the environment at import time).

- [ ] **Step 1: Add the `mcp` dependency**

In `backend/pyproject.toml`, add this line to the `dependencies` array (after the `apscheduler`
line, matching the existing per-entry comment style):

```toml
    "apscheduler>=3.10",
    # MCP stdio server for the incident-chat fetch_logs tool (design spec 2026-08-23) — spawned by
    # `claude -p --mcp-config` as a short-lived subprocess per chat turn.
    "mcp>=1.2",
```

Run: `cd backend && uv lock`
Expected: `uv.lock` is updated with `mcp` and its transitive dependencies (no errors).

Run: `cd backend && uv sync`
Expected: `mcp` installs into the local `.venv` without error (so the test in Step 4 below can
import it locally, not just inside Docker).

- [ ] **Step 2: Write the MCP server module**

Create `backend/app/infrastructure/llm/mcp_log_tool.py`:

```python
"""MCP stdio server exposing `fetch_logs` to the incident-chat Claude Code CLI session (design
spec .claude/specs/2026-08-23-incident-chat-design.md).

Spawned by `claude -p --mcp-config ...` as a short-lived subprocess per chat turn — this is NOT
part of the FastAPI process. It runs in the same container image, so it imports this app's own
log-fetching code directly (`build_log_fetcher`) instead of calling back over HTTP.

`IIM_INCIDENT_SERVICE` (set in the MCP config's `env`, see `ClaudeCliChat._run_turn` in
`claude_cli.py`) scopes every call to one incident's project. It is read from the environment, not
a tool parameter, so Claude cannot ask this tool to fetch a different project's logs.
"""

from __future__ import annotations

import os
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from app.infrastructure.config import get_settings
from app.infrastructure.logs.factory import build_log_fetcher

mcp = FastMCP("iim-tools")
_SERVICE = os.environ["IIM_INCIDENT_SERVICE"]


@mcp.tool()
async def fetch_logs(
    log_group: str, start: str, end: str, filter_pattern: str | None = None
) -> str:
    """Fetch recent log lines for this incident's service from its CloudWatch (or demo) log
    group. `start`/`end` are ISO-8601 timestamps. `filter_pattern` is an optional CloudWatch Logs
    Insights `like` regex; omit it to use the default error-pattern filter."""
    try:
        fetcher = build_log_fetcher(_SERVICE, get_settings())
        events = await fetcher.fetch_logs(
            log_group,
            datetime.fromisoformat(start),
            datetime.fromisoformat(end),
            filter_pattern,
        )
    except Exception as exc:  # noqa: BLE001 - report the failure back to Claude as tool output, don't crash the MCP server
        return f"fetch_logs failed: {exc}"
    if not events:
        return "(no matching log lines in this window)"
    return "\n".join(f"{e.timestamp.isoformat()} {e.level or '-'} {e.message}" for e in events)


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 3: Write the failing test**

Create `backend/tests/test_mcp_log_tool.py`:

```python
"""Unit tests for the fetch_logs MCP tool function — calls it directly (no MCP transport, no
subprocess), monkeypatching build_log_fetcher so no real AWS/demo call happens."""

import os

os.environ.setdefault("IIM_INCIDENT_SERVICE", "GCM")  # read at module import time

from datetime import datetime

import pytest

from app.domain.incidents.entities import LogEvent
from app.infrastructure.llm import mcp_log_tool

pytestmark = pytest.mark.asyncio


class _FakeFetcher:
    def __init__(self, events=None, raises=None):
        self._events = events or []
        self._raises = raises

    async def fetch_logs(self, log_group, start, end, filter_pattern=None):
        if self._raises:
            raise self._raises
        return self._events


async def test_fetch_logs_returns_formatted_lines(monkeypatch):
    events = [LogEvent(timestamp=datetime(2026, 8, 23, 10, 0), message="boom", level="ERROR")]
    monkeypatch.setattr(
        mcp_log_tool, "build_log_fetcher", lambda service, settings: _FakeFetcher(events=events)
    )

    result = await mcp_log_tool.fetch_logs.fn(
        log_group="/ecs/prod", start="2026-08-23T09:00:00", end="2026-08-23T11:00:00"
    )

    assert "boom" in result
    assert "ERROR" in result


async def test_fetch_logs_reports_no_matches(monkeypatch):
    monkeypatch.setattr(
        mcp_log_tool, "build_log_fetcher", lambda service, settings: _FakeFetcher(events=[])
    )

    result = await mcp_log_tool.fetch_logs.fn(
        log_group="/ecs/prod", start="2026-08-23T09:00:00", end="2026-08-23T11:00:00"
    )

    assert result == "(no matching log lines in this window)"


async def test_fetch_logs_reports_fetcher_errors_as_text_not_a_crash(monkeypatch):
    monkeypatch.setattr(
        mcp_log_tool,
        "build_log_fetcher",
        lambda service, settings: _FakeFetcher(raises=RuntimeError("SSO token expired")),
    )

    result = await mcp_log_tool.fetch_logs.fn(
        log_group="/ecs/prod", start="2026-08-23T09:00:00", end="2026-08-23T11:00:00"
    )

    assert "fetch_logs failed" in result
    assert "SSO token expired" in result
```

Note: `FastMCP`'s `@mcp.tool()` decorator wraps the function in a `Tool` object; the original
async function is reachable via `.fn` (confirm this against the installed `mcp` package version —
if the attribute name differs, `python3 -c "from app.infrastructure.llm.mcp_log_tool import fetch_logs; print(dir(fetch_logs))"` shows the actual wrapper's attributes).

- [ ] **Step 4: Run it**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest tests/test_mcp_log_tool.py -v`
Expected: 3 passed. If `.fn` doesn't exist on the installed `mcp` version, adjust Step 3's access
pattern to whatever `dir(fetch_logs)` shows (e.g. some versions expose `.func` or the tool object
is itself callable) and re-run.

- [ ] **Step 5: Verify the module actually runs as a subprocess (manual smoke test, not pytest)**

Run: `cd backend && IIM_INCIDENT_SERVICE=GCM DEMO_LOGS=true timeout 3 uv run python3 -m app.infrastructure.llm.mcp_log_tool < /dev/null`
Expected: the process starts and waits on stdio (an MCP stdio server blocks reading JSON-RPC
messages from stdin) — it should NOT crash with an import error or `KeyError:
'IIM_INCIDENT_SERVICE'` before the 3-second timeout kills it. A clean timeout-kill (exit code 124)
is success here; a traceback printed to stderr before that is a real failure to fix.

- [ ] **Step 6: Lint**

Run: `cd backend && uv run ruff check app/infrastructure/llm/mcp_log_tool.py tests/test_mcp_log_tool.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
cd /home/hieuly/hieuly/project/LLM-SRE
git add backend/pyproject.toml backend/uv.lock backend/app/infrastructure/llm/mcp_log_tool.py backend/tests/test_mcp_log_tool.py
git commit -m "feat: add fetch_logs MCP tool server for incident chat"
```

---

### Task 4: `claude_cli.py` — `ClaudeCliChat` (chat turn with MCP tool-calling + session resume)

**Files:**
- Modify: `backend/app/infrastructure/llm/claude_cli.py`
- Test: `backend/tests/test_claude_cli.py`

**Interfaces:**
- Consumes: `ChatTurnResult` (Task 1), `_CliResult`/`_get_token` (existing, this file).
- Produces: `class ClaudeCliChat` with `async def send(self, *, service: str, context: dict, message: str, claude_session_id: uuid.UUID, is_new_session: bool) -> ChatTurnResult` — implements `IncidentChatProvider` (Task 1) structurally (no explicit inheritance needed, Python Protocols are structural).

- [ ] **Step 1: Refactor the shared subprocess/parse logic out of `_call_claude_cli`**

In `backend/app/infrastructure/llm/claude_cli.py`, replace the existing `_call_claude_cli`
function (the one that builds `cmd` and does the subprocess call) with this split — the behavior
of `_call_claude_cli` itself is unchanged, just factored so the new chat path can reuse the
subprocess-running/parsing half without the `--tools ""` half:

```python
async def _run_cli(cmd: list[str], token: str) -> _CliResult:
    """Run an already-built `claude` command, authenticated via CLAUDE_CODE_OAUTH_TOKEN, and
    parse the JSON output into a _CliResult. Raises RuntimeError on any CLI-reported failure."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # API key would silently outrank the OAuth token
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )
    stdout, stderr = await proc.communicate()

    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claude CLI returned non-JSON output (exit {proc.returncode}): "
            f"{stdout.decode(errors='replace')[:500]}"
        ) from exc

    if data.get("is_error") or proc.returncode != 0:
        detail = data.get("result") or stderr.decode(errors="replace") or "unknown error"
        raise RuntimeError(f"claude CLI failed: {detail}")

    usage = data.get("usage") or {}
    # "input_tokens" alone massively undercounts: --safe-mode still caches most of the prompt
    # (system prompt / project context) across calls in the same OAuth session, and that shows up
    # as cache_read_input_tokens / cache_creation_input_tokens instead — both still real spend
    # against the account, just billed at a different (cheaper) rate than fresh input tokens.
    input_tokens = (
        usage.get("input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        if "input_tokens" in usage
        else None
    )
    return _CliResult(
        text=data["result"],
        input_tokens=input_tokens,
        output_tokens=usage.get("output_tokens"),
    )


async def _call_claude_cli(
    *, prompt: str, system_prompt: str | None, model: str, token: str
) -> _CliResult:
    """Run `claude -p` headless with no tools at all — analysis / graph-mode / daily-report calls
    never need Claude to act autonomously (only incident chat does; see ClaudeCliChat below)."""
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--tools", "", "--safe-mode", "--model", model]
    if system_prompt:
        cmd.extend(["--append-system-prompt", system_prompt])
    return await _run_cli(cmd, token)
```

- [ ] **Step 2: Run the existing tests to confirm the refactor changed nothing observable**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest tests/test_claude_cli.py -v`
Expected: the same tests that passed before this step still pass (10 passed, per this repo's
current state) — `_call_claude_cli`'s public behavior is unchanged.

- [ ] **Step 3: Add the chat system prompt, `ChatTurnResult` import, and `ClaudeCliChat` class**

In `backend/app/infrastructure/llm/claude_cli.py`, change the import line:

```python
from app.domain.incidents.entities import AnalysisDraft
```

to:

```python
from app.domain.incidents.entities import AnalysisDraft, ChatTurnResult
```

Also add `import uuid` near the top (alongside the existing `import asyncio`, `import json`,
`import os`).

Append this at the end of the file (after `ClaudeCliChatModel`):

```python
_CHAT_SYSTEM_PROMPT = """You are a senior SRE helping investigate one specific incident in a chat conversation. \
You have the incident's full raw context below. Answer the user's questions grounded in that context. \
If you need log lines you don't already have to answer well, call the fetch_logs tool rather than guessing — \
it fetches real log lines for this incident's own service. Never invent log content, metrics, or events \
that aren't in the context or in a tool result. Be concise and direct, like an engineer working the incident live."""


def _chat_system_prompt(context: dict) -> str:
    return f"{_CHAT_SYSTEM_PROMPT}\n\nIncident context:\n{json.dumps(context, indent=2, default=str)}"


class ClaudeCliChat:
    """Runs one incident-chat turn through the Claude Code CLI's own agentic tool-use loop (an
    MCP server exposing `fetch_logs`, see `mcp_log_tool.py`) — distinct from `ClaudeCliChatModel`,
    which returns plain text with no tools enabled. Only this class needs tools, because incident
    chat is the first feature where Claude must be able to act autonomously mid-call (design spec
    .claude/specs/2026-08-23-incident-chat-design.md)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(
        self,
        *,
        service: str,
        context: dict,
        message: str,
        claude_session_id: uuid.UUID,
        is_new_session: bool,
    ) -> ChatTurnResult:
        token = await _get_token(self._settings)
        system_prompt = _chat_system_prompt(context)
        try:
            result = await self._run_turn(
                service=service,
                message=message,
                system_prompt=system_prompt,
                token=token,
                session_id=claude_session_id,
                resume=not is_new_session,
            )
            return ChatTurnResult(
                text=result.text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                claude_session_id=claude_session_id,
            )
        except RuntimeError:
            if is_new_session:
                raise  # a fresh session failing outright is a real error, not a stale-resume problem
            # --resume pointed at a session the CLI no longer has (e.g. the backend container was
            # recreated between turns) — start a fresh session rather than hard-failing the chat.
            new_session_id = uuid.uuid4()
            result = await self._run_turn(
                service=service,
                message=message,
                system_prompt=system_prompt,
                token=token,
                session_id=new_session_id,
                resume=False,
            )
            return ChatTurnResult(
                text=result.text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                claude_session_id=new_session_id,
            )

    async def _run_turn(
        self,
        *,
        service: str,
        message: str,
        system_prompt: str,
        token: str,
        session_id: uuid.UUID,
        resume: bool,
    ) -> _CliResult:
        # The MCP server subprocess is spawned BY `claude`, not by us — it needs the same
        # PROJECT_<SERVICE>_*/DEMO_LOGS/AWS_* config this process has, not just the one extra
        # variable, so build_log_fetcher() works inside it. Merging the full environment here is
        # correct regardless of whether `claude` additively merges or replaces inherited env for
        # its MCP children.
        mcp_env = {**os.environ, "IIM_INCIDENT_SERVICE": service}
        mcp_config = json.dumps(
            {
                "mcpServers": {
                    "iim-tools": {
                        "command": "python3",
                        "args": ["-m", "app.infrastructure.llm.mcp_log_tool"],
                        "env": mcp_env,
                    }
                }
            }
        )
        cmd = [
            "claude", "-p", message,
            "--output-format", "json",
            "--mcp-config", mcp_config,
            "--strict-mcp-config",
            "--allowedTools", "mcp__iim-tools__fetch_logs",
            "--safe-mode", "--model", self._settings.claude_cli_model,
            "--append-system-prompt", system_prompt,
            "--resume" if resume else "--session-id", str(session_id),
        ]
        return await _run_cli(cmd, token)
```

- [ ] **Step 4: Write the failing tests**

Append to `backend/tests/test_claude_cli.py`:

```python
async def test_chat_sends_session_id_for_a_new_session(monkeypatch):
    calls = []

    async def fake_run_cli(cmd, token):
        calls.append(cmd)
        return claude_cli._CliResult(text="answer", input_tokens=50, output_tokens=20)

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_run_cli", fake_run_cli)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    chat = claude_cli.ClaudeCliChat(_settings())
    session_id = uuid.uuid4()
    result = await chat.send(
        service="GCM", context={"service": "GCM"}, message="check logs",
        claude_session_id=session_id, is_new_session=True,
    )

    assert result.text == "answer"
    assert result.claude_session_id == session_id
    assert len(calls) == 1
    assert "--session-id" in calls[0]
    assert "--resume" not in calls[0]
    assert "--mcp-config" in calls[0]
    assert "--strict-mcp-config" in calls[0]
    assert "mcp__iim-tools__fetch_logs" in calls[0]


async def test_chat_resumes_an_existing_session(monkeypatch):
    calls = []

    async def fake_run_cli(cmd, token):
        calls.append(cmd)
        return claude_cli._CliResult(text="follow-up answer", input_tokens=5, output_tokens=3)

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_run_cli", fake_run_cli)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    chat = claude_cli.ClaudeCliChat(_settings())
    session_id = uuid.uuid4()
    result = await chat.send(
        service="GCM", context={}, message="and then?",
        claude_session_id=session_id, is_new_session=False,
    )

    assert result.claude_session_id == session_id
    assert "--resume" in calls[0]
    assert "--session-id" not in calls[0]


async def test_chat_falls_back_to_a_fresh_session_when_resume_fails(monkeypatch):
    calls = []

    async def fake_run_cli(cmd, token):
        calls.append(cmd)
        if "--resume" in cmd:
            raise RuntimeError("claude CLI failed: No conversation found with session ID")
        return claude_cli._CliResult(text="fresh answer", input_tokens=1, output_tokens=1)

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_run_cli", fake_run_cli)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    chat = claude_cli.ClaudeCliChat(_settings())
    stale_session_id = uuid.uuid4()
    result = await chat.send(
        service="GCM", context={}, message="hi again",
        claude_session_id=stale_session_id, is_new_session=False,
    )

    assert result.text == "fresh answer"
    assert result.claude_session_id != stale_session_id
    assert len(calls) == 2
    assert "--resume" in calls[0]
    assert "--session-id" in calls[1]


async def test_chat_raises_when_a_brand_new_session_fails_outright(monkeypatch):
    async def fake_run_cli(cmd, token):
        raise RuntimeError("claude CLI failed: 401 Invalid bearer token")

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_run_cli", fake_run_cli)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    chat = claude_cli.ClaudeCliChat(_settings())
    with pytest.raises(RuntimeError, match="401"):
        await chat.send(
            service="GCM", context={}, message="hi",
            claude_session_id=uuid.uuid4(), is_new_session=True,
        )
```

Add `import uuid` at the top of `test_claude_cli.py` alongside the existing `import pytest` line
(check the file's current imports first — if `uuid` is already imported there, skip this).

- [ ] **Step 5: Run the tests**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest tests/test_claude_cli.py -v`
Expected: 14 passed (10 existing + 4 new).

- [ ] **Step 6: Lint**

Run: `cd backend && uv run ruff check app/infrastructure/llm/claude_cli.py tests/test_claude_cli.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
cd /home/hieuly/hieuly/project/LLM-SRE
git add backend/app/infrastructure/llm/claude_cli.py backend/tests/test_claude_cli.py
git commit -m "feat: add ClaudeCliChat for incident chat with MCP tool-calling and session resume"
```

---

### Task 5: Application layer — `IncidentChat` use case

**Files:**
- Create: `backend/app/application/incidents/chat.py`
- Test: `backend/tests/test_incident_chat_usecase.py` (new)

**Interfaces:**
- Consumes: `IncidentRepository`, `ChatRepository` (Task 1), `IncidentChatProvider` (Task 1), `UnitOfWork` (existing, `app.domain.shared`).
- Produces: `class IncidentChat` (dataclass) with `async def send_message(self, incident: Incident, message: str) -> ChatMessage`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_incident_chat_usecase.py`:

```python
"""Unit tests for the IncidentChat use case using in-memory fakes — no DB, no subprocess."""

import uuid
from datetime import datetime, timezone

import pytest

from app.application.incidents.chat import IncidentChat
from app.domain.incidents.entities import ChatMessage, ChatSession, ChatTurnResult, Incident

pytestmark = pytest.mark.asyncio


class FakeChatRepo:
    def __init__(self):
        self.sessions: dict[uuid.UUID, ChatSession] = {}
        self.messages: list[ChatMessage] = []

    async def get_or_create_session(self, incident_id):
        if incident_id not in self.sessions:
            self.sessions[incident_id] = ChatSession(
                incident_id=incident_id, claude_session_id=uuid.uuid4()
            )
        return self.sessions[incident_id]

    async def replace_session_id(self, incident_id, claude_session_id):
        existing = self.sessions[incident_id]
        self.sessions[incident_id] = ChatSession(
            incident_id=incident_id, claude_session_id=claude_session_id
        )

    async def add_message(self, message):
        stored = ChatMessage(
            incident_id=message.incident_id, role=message.role, content=message.content,
            input_tokens=message.input_tokens, output_tokens=message.output_tokens,
            id=uuid.uuid4(), created_at=datetime.now(timezone.utc),
        )
        self.messages.append(stored)
        return stored

    async def list_messages(self, incident_id):
        return [m for m in self.messages if m.incident_id == incident_id]


class FakeIncidentRepo:
    async def get(self, incident_id):
        return None  # not used by IncidentChat — it receives the Incident directly


class FakeChatProvider:
    def __init__(self, result: ChatTurnResult):
        self._result = result
        self.calls = []

    async def send(self, *, service, context, message, claude_session_id, is_new_session):
        self.calls.append(
            {
                "service": service, "context": context, "message": message,
                "claude_session_id": claude_session_id, "is_new_session": is_new_session,
            }
        )
        return self._result


class FakeUnitOfWork:
    async def commit(self):
        pass


def _incident() -> Incident:
    return Incident(
        service="GCM", source="manual", fingerprint="fp", context={"service": "GCM", "alert": "x"},
        id=uuid.uuid4(),
    )


async def test_first_message_is_a_new_session(monkeypatch=None):
    incident = _incident()
    chat_repo = FakeChatRepo()
    session_id_from_provider = uuid.uuid4()
    provider = FakeChatProvider(
        ChatTurnResult(
            text="here's what I see", input_tokens=100, output_tokens=40,
            claude_session_id=session_id_from_provider,
        )
    )
    use_case = IncidentChat(
        incidents=FakeIncidentRepo(), chat=chat_repo, claude_chat=provider, uow=FakeUnitOfWork()
    )

    reply = await use_case.send_message(incident, "what happened here?")

    assert provider.calls[0]["is_new_session"] is True
    assert provider.calls[0]["message"] == "what happened here?"
    assert provider.calls[0]["context"] == incident.context
    assert reply.role == "assistant"
    assert reply.content == "here's what I see"
    assert reply.input_tokens == 100
    assert reply.output_tokens == 40

    stored = await chat_repo.list_messages(incident.id)
    assert [m.role for m in stored] == ["user", "assistant"]
    assert stored[0].content == "what happened here?"


async def test_second_message_resumes_the_session():
    incident = _incident()
    chat_repo = FakeChatRepo()
    existing_session = await chat_repo.get_or_create_session(incident.id)
    await chat_repo.add_message(ChatMessage(incident_id=incident.id, role="user", content="first"))
    await chat_repo.add_message(ChatMessage(incident_id=incident.id, role="assistant", content="reply"))

    provider = FakeChatProvider(
        ChatTurnResult(
            text="second reply", input_tokens=10, output_tokens=5,
            claude_session_id=existing_session.claude_session_id,
        )
    )
    use_case = IncidentChat(
        incidents=FakeIncidentRepo(), chat=chat_repo, claude_chat=provider, uow=FakeUnitOfWork()
    )

    await use_case.send_message(incident, "and then?")

    assert provider.calls[0]["is_new_session"] is False
    assert provider.calls[0]["claude_session_id"] == existing_session.claude_session_id


async def test_replaces_stored_session_id_when_the_provider_started_a_fresh_one():
    incident = _incident()
    chat_repo = FakeChatRepo()
    original_session = await chat_repo.get_or_create_session(incident.id)
    await chat_repo.add_message(ChatMessage(incident_id=incident.id, role="user", content="first"))

    fresh_session_id = uuid.uuid4()
    provider = FakeChatProvider(
        ChatTurnResult(
            text="restarted", input_tokens=1, output_tokens=1, claude_session_id=fresh_session_id
        )
    )
    use_case = IncidentChat(
        incidents=FakeIncidentRepo(), chat=chat_repo, claude_chat=provider, uow=FakeUnitOfWork()
    )

    await use_case.send_message(incident, "hi")

    updated = await chat_repo.get_or_create_session(incident.id)
    assert updated.claude_session_id == fresh_session_id
    assert updated.claude_session_id != original_session.claude_session_id
```

- [ ] **Step 2: Run it to verify it fails (module doesn't exist yet)**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest tests/test_incident_chat_usecase.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.application.incidents.chat'`.

- [ ] **Step 3: Write the use case**

Create `backend/app/application/incidents/chat.py`:

```python
"""IncidentChat use case: one turn of the per-incident chat (design spec
.claude/specs/2026-08-23-incident-chat-design.md).

Persists both the user's message and the assistant's reply, and keeps the stored
`claude_session_id` in sync when the provider had to start a fresh session (see
`ClaudeCliChat.send`'s docstring in `infrastructure/llm/claude_cli.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.incidents.entities import ChatMessage, Incident
from app.domain.incidents.ports import ChatRepository, IncidentRepository
from app.domain.llm import IncidentChatProvider
from app.domain.shared import UnitOfWork


@dataclass
class IncidentChat:
    incidents: IncidentRepository
    chat: ChatRepository
    claude_chat: IncidentChatProvider
    uow: UnitOfWork

    async def send_message(self, incident: Incident, message: str) -> ChatMessage:
        session = await self.chat.get_or_create_session(incident.id)
        existing = await self.chat.list_messages(incident.id)
        is_new_session = len(existing) == 0

        await self.chat.add_message(
            ChatMessage(incident_id=incident.id, role="user", content=message)
        )

        result = await self.claude_chat.send(
            service=incident.service,
            context=incident.context,
            message=message,
            claude_session_id=session.claude_session_id,
            is_new_session=is_new_session,
        )
        if result.claude_session_id != session.claude_session_id:
            await self.chat.replace_session_id(incident.id, result.claude_session_id)

        reply = await self.chat.add_message(
            ChatMessage(
                incident_id=incident.id,
                role="assistant",
                content=result.text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        )
        await self.uow.commit()
        return reply
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest tests/test_incident_chat_usecase.py -v`
Expected: 3 passed.

- [ ] **Step 5: Lint**

Run: `cd backend && uv run ruff check app/application/incidents/chat.py tests/test_incident_chat_usecase.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
cd /home/hieuly/hieuly/project/LLM-SRE
git add backend/app/application/incidents/chat.py backend/tests/test_incident_chat_usecase.py
git commit -m "feat: add IncidentChat use case"
```

---

### Task 6: Interface layer — chat endpoints, DTOs, deps wiring; remove the log-search endpoint

**Files:**
- Modify: `backend/app/interface/http/dto/request/incident.py`
- Modify: `backend/app/interface/http/dto/request/__init__.py`
- Modify: `backend/app/interface/http/dto/response/incident.py`
- Modify: `backend/app/interface/http/dto/response/__init__.py`
- Modify: `backend/app/interface/http/dto/mappers/incident.py`
- Modify: `backend/app/interface/http/deps.py`
- Modify: `backend/app/interface/http/incidents.py`
- Modify: `backend/tests/test_incidents_http.py` (remove log-search tests)
- Test: `backend/tests/test_incident_chat_http.py` (new)

**Interfaces:**
- Consumes: `IncidentChat` (Task 5), `ChatRepository`/`ClaudeCliChat` (Tasks 1, 4), `mappers.chat_message_out` (this task).
- Produces: `GET /api/incidents/{id}/chat` → `list[ChatMessageOut]`; `POST /api/incidents/{id}/chat` `{message}` → `ChatMessageOut`; `get_chat_repository`, `get_incident_chat` in `deps.py`.

- [ ] **Step 1: Request DTO — remove `LogSearchRequest`, add `ChatMessageRequest`**

Replace the full content of `backend/app/interface/http/dto/request/incident.py` with:

```python
"""Incident request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IncidentIngestRequest(BaseModel):
    """`POST /api/incidents` body: a source label plus the raw incident context dict."""

    source: str = "manual"  # auto | manual | webhook
    context: dict = Field(..., description="Incident context; must contain 'service'.")


class ChatMessageRequest(BaseModel):
    """`POST /api/incidents/{id}/chat` body."""

    message: str = Field(..., min_length=1)


class ResolveIncidentRequest(BaseModel):
    """`POST /api/incidents/{id}/resolve` body: how the incident was actually fixed."""

    resolution_notes: str = Field(..., min_length=1)
```

(`datetime` import is dropped — it was only used by the now-removed `LogSearchRequest`.)

- [ ] **Step 2: Update `dto/request/__init__.py`**

Replace its content with:

```python
"""Inbound request DTOs, one module per resource."""

from app.interface.http.dto.request.auth import AdminLoginRequest
from app.interface.http.dto.request.cloud_connection import CloudConnectionCreateRequest
from app.interface.http.dto.request.document import DocumentIngestRequest
from app.interface.http.dto.request.incident import (
    ChatMessageRequest,
    IncidentIngestRequest,
    ResolveIncidentRequest,
)
from app.interface.http.dto.request.settings import SetTokenRequest

__all__ = [
    "IncidentIngestRequest",
    "ChatMessageRequest",
    "ResolveIncidentRequest",
    "DocumentIngestRequest",
    "CloudConnectionCreateRequest",
    "SetTokenRequest",
    "AdminLoginRequest",
]
```

- [ ] **Step 3: Response DTO — remove `LogEventOut`/`LogSearchResult`, add `ChatMessageOut`**

In `backend/app/interface/http/dto/response/incident.py`, delete the `LogEventOut` and
`LogSearchResult` classes entirely (the last two classes in the file), and add this in their
place:

```python
class ChatMessageOut(BaseModel):
    """One row in `GET /api/incidents/{id}/chat`, and the response of `POST .../chat`."""

    id: uuid.UUID
    role: str  # user | assistant
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime
```

- [ ] **Step 4: Update `dto/response/__init__.py`**

Replace its content with:

```python
"""Outbound response DTOs, one module per resource."""

from app.interface.http.dto.response.auth import AdminLoginResponse
from app.interface.http.dto.response.cloud_connection import (
    CloudConnectionOut,
    PollResult,
    PollScheduleOut,
    TestConnectionResult,
)
from app.interface.http.dto.response.document import DocumentCreatedResponse, DocumentSummary
from app.interface.http.dto.response.health import HealthResponse
from app.interface.http.dto.response.incident import (
    AnalysisOut,
    ChatMessageOut,
    IncidentCreatedResponse,
    IncidentDetail,
    IncidentSummary,
    KnownIssueOut,
)
from app.interface.http.dto.response.report import DailyReportOut, ReportIncidentOut
from app.interface.http.dto.response.settings import LlmUsageOut, SettingStatus, UsageByModelOut

__all__ = [
    "IncidentCreatedResponse",
    "AnalysisOut",
    "IncidentSummary",
    "IncidentDetail",
    "KnownIssueOut",
    "ChatMessageOut",
    "DocumentCreatedResponse",
    "DocumentSummary",
    "HealthResponse",
    "DailyReportOut",
    "ReportIncidentOut",
    "CloudConnectionOut",
    "TestConnectionResult",
    "PollResult",
    "PollScheduleOut",
    "SettingStatus",
    "LlmUsageOut",
    "UsageByModelOut",
    "AdminLoginResponse",
]
```

- [ ] **Step 5: Mapper — add `chat_message_out`**

In `backend/app/interface/http/dto/mappers/incident.py`, change the imports:

```python
from app.domain.incidents.entities import Analysis, ChatMessage, Incident
from app.interface.http.dto.response.incident import (
    AnalysisOut,
    ChatMessageOut,
    IncidentDetail,
    IncidentSummary,
    KnownIssueOut,
)
```

Append this function at the end of the file:

```python
def chat_message_out(message: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        input_tokens=message.input_tokens,
        output_tokens=message.output_tokens,
        created_at=message.created_at,
    )
```

- [ ] **Step 6: Wire dependencies in `deps.py`**

In `backend/app/interface/http/deps.py`:

Add these imports (alongside the existing ones from the same modules):

```python
from app.application.incidents.chat import IncidentChat
from app.domain.incidents.ports import ChatRepository, IncidentRepository, LogFetcher, TicketClient
from app.infrastructure.db.repositories import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyAppSettingsRepository,
    SqlAlchemyChatRepository,
    SqlAlchemyCloudConnectionRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyIncidentRepository,
    SqlAlchemyRetriever,
    SqlAlchemyTrackedAlarmRepository,
    SqlAlchemyUnitOfWork,
)
from app.infrastructure.llm.claude_cli import ClaudeCliAnalyzer, ClaudeCliChat, ClaudeCliChatModel
```

(These replace the existing single-line versions of the `app.domain.incidents.ports` import, the
`app.infrastructure.db.repositories` multi-import, and the `app.infrastructure.llm.claude_cli`
import — add `ChatRepository` / `SqlAlchemyChatRepository` / `ClaudeCliChat` to each respectively,
keep everything else in those import blocks unchanged.)

Add these two functions (near `get_log_fetcher_factory`, since both are incident-detail-page
support dependencies):

```python
def get_chat_repository(session: AsyncSession = Depends(get_session)) -> ChatRepository:
    return SqlAlchemyChatRepository(session)


def get_incident_chat(
    session: AsyncSession = Depends(get_session),
    incidents: IncidentRepository = Depends(get_incident_repository),
    chat: ChatRepository = Depends(get_chat_repository),
) -> IncidentChat:
    return IncidentChat(
        incidents=incidents,
        chat=chat,
        claude_chat=ClaudeCliChat(get_settings()),
        uow=SqlAlchemyUnitOfWork(session),
    )
```

- [ ] **Step 7: Replace the log-search endpoint with the chat endpoints in `incidents.py`**

In `backend/app/interface/http/incidents.py`, change the imports at the top:

```python
from app.domain.incidents.ports import IncidentRepository, LogFetcher, TicketClient
```

to:

```python
from app.domain.incidents.ports import ChatRepository, IncidentRepository, TicketClient
```

(`LogFetcher` is dropped — it was only used by `search_incident_logs`'s type hint.)

Change:

```python
from app.interface.http.deps import (
    get_document_repository,
    get_event_bus,
    get_incident_repository,
    get_ingest_incident,
    get_log_fetcher_factory,
    get_resolve_incident,
    get_ticket_client,
    get_unit_of_work,
    resolve_background_incident_deps,
)
```

to:

```python
from app.infrastructure.config import Settings, get_settings
from app.interface.http.deps import (
    get_chat_repository,
    get_document_repository,
    get_event_bus,
    get_incident_chat,
    get_incident_repository,
    get_ingest_incident,
    get_resolve_incident,
    get_ticket_client,
    get_unit_of_work,
    resolve_background_incident_deps,
)
```

Change:

```python
from app.interface.http.dto.request import (
    IncidentIngestRequest,
    LogSearchRequest,
    ResolveIncidentRequest,
)
from app.interface.http.dto.response import (
    IncidentCreatedResponse,
    IncidentDetail,
    IncidentSummary,
    LogEventOut,
    LogSearchResult,
)
```

to:

```python
from app.application.incidents.chat import IncidentChat
from app.interface.http.dto.request import (
    ChatMessageRequest,
    IncidentIngestRequest,
    ResolveIncidentRequest,
)
from app.interface.http.dto.response import (
    ChatMessageOut,
    IncidentCreatedResponse,
    IncidentDetail,
    IncidentSummary,
)
```

Also change `from collections.abc import AsyncIterator, Callable` to `from collections.abc import
AsyncIterator` (`Callable` was only used by `search_incident_logs`'s `log_fetcher_factory`
parameter type).

Replace the entire `search_incident_logs` function (from `@router.post("/{incident_id}/logs/search"...)` through its closing `)` before `@router.get("", response_model=list[IncidentSummary])`) with:

```python
@router.get("/{incident_id}/chat", response_model=list[ChatMessageOut])
async def list_chat_messages(
    incident_id: uuid.UUID,
    repo: IncidentRepository = Depends(get_incident_repository),
    chat: ChatRepository = Depends(get_chat_repository),
) -> list[ChatMessageOut]:
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    messages = await chat.list_messages(incident_id)
    return [mappers.chat_message_out(m) for m in messages]


@router.post("/{incident_id}/chat", response_model=ChatMessageOut)
async def send_chat_message(
    incident_id: uuid.UUID,
    body: ChatMessageRequest,
    repo: IncidentRepository = Depends(get_incident_repository),
    incident_chat: IncidentChat = Depends(get_incident_chat),
    settings: Settings = Depends(get_settings),
) -> ChatMessageOut:
    """Chat about one incident, grounded in its raw context — Claude can autonomously call a
    fetch_logs tool mid-conversation (design spec .claude/specs/2026-08-23-incident-chat-design.md).
    Only implemented for LLM_PROVIDER=claude_cli — see that spec's "Why claude_cli only" section."""
    if settings.llm_provider != "claude_cli":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="incident chat requires LLM_PROVIDER=claude_cli",
        )
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    reply = await incident_chat.send_message(incident, body.message)
    return mappers.chat_message_out(reply)
```

- [ ] **Step 8: Remove the old log-search tests**

In `backend/tests/test_incidents_http.py`, delete the test functions `test_log_search_merges_logs_and_reanalyzes` and `test_log_search_404_for_unknown_incident` entirely.

Run: `grep -n "_FakeLogFetcher\|get_log_fetcher_factory\|LogFetcher" backend/tests/test_incidents_http.py`

If that grep shows any remaining references (the fixture's `app.dependency_overrides[get_log_fetcher_factory] = ...` line and the `_FakeLogFetcher` class definition, plus the corresponding import line), delete those too — they only existed to support the two tests just removed. Re-run the grep after editing to confirm it now returns nothing.

- [ ] **Step 9: Write the failing test for the new chat endpoints**

Create `backend/tests/test_incident_chat_http.py`:

```python
"""HTTP tests for GET/POST /api/incidents/{id}/chat — overrides get_incident_chat with a fake
IncidentChat (no real subprocess) and get_session with a real Postgres session (iim_test)."""

import os
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.incidents.entities import ChatMessage
from app.infrastructure.db.orm import Base, ChatMessageRow, ChatSessionRow, IncidentRow
from app.interface.http.deps import get_incident_chat, get_session
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)


class _FakeIncidentChat:
    async def send_message(self, incident, message):
        return ChatMessage(
            incident_id=incident.id, role="assistant", content=f"echo: {message}",
            input_tokens=10, output_tokens=5, id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
        )


@pytest.fixture()
async def client():
    engine = create_async_engine(_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"Postgres not reachable: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(ChatMessageRow))
        await s.execute(delete(ChatSessionRow))
        await s.execute(delete(IncidentRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_incident_chat] = lambda: _FakeIncidentChat()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def _create_incident(client) -> str:
    r = await client.post(
        "/api/incidents", json={"source": "manual", "context": {"service": "GCM"}}
    )
    return r.json()["incident_id"]


async def test_post_chat_returns_the_assistant_reply(client):
    incident_id = await _create_incident(client)

    r = await client.post(f"/api/incidents/{incident_id}/chat", json={"message": "hi"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "assistant"
    assert body["content"] == "echo: hi"
    assert body["input_tokens"] == 10
    assert body["output_tokens"] == 5


async def test_post_chat_404_for_unknown_incident(client):
    r = await client.post(
        "/api/incidents/00000000-0000-0000-0000-000000000000/chat", json={"message": "hi"}
    )
    assert r.status_code == 404


async def test_post_chat_rejects_empty_message(client):
    incident_id = await _create_incident(client)
    r = await client.post(f"/api/incidents/{incident_id}/chat", json={"message": ""})
    assert r.status_code == 422


async def test_get_chat_lists_messages_after_posting(client):
    incident_id = await _create_incident(client)
    await client.post(f"/api/incidents/{incident_id}/chat", json={"message": "hi"})

    r = await client.get(f"/api/incidents/{incident_id}/chat")

    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["role"] == "assistant"


async def test_get_chat_404_for_unknown_incident(client):
    r = await client.get("/api/incidents/00000000-0000-0000-0000-000000000000/chat")
    assert r.status_code == 404
```

Note: `_create_incident` posts through the real `/api/incidents` create flow, which schedules a
background analysis task — since `get_base_analyzer`/`get_embedder` are NOT overridden in this
fixture, that background task will attempt a real Bedrock/embedding call and fail asynchronously.
That failure is harmless to these chat tests (it only sets the incident's `status` to `failed`
sometime after the response returns; nothing here asserts on `status`), but if it turns out to
cause flakiness or log noise, override `get_base_analyzer`/`get_embedder` with the same fakes
`test_incidents_http.py` uses instead of writing incidents through the full create endpoint —
inspect that file's `_FakeAnalyzer`/`_FakeEmbedder` fixture setup and mirror it here if needed.

- [ ] **Step 10: Run the new tests**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest tests/test_incident_chat_http.py -v`
Expected: 5 passed.

- [ ] **Step 11: Run the full backend suite + lint**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://iim:change-me@localhost:5432/iim_test" uv run pytest -q && uv run ruff check app/ tests/`
Expected: same 5 pre-existing pgvector-dimension failures as the baseline, no others; the two
removed log-search tests are gone from the count; ruff clean.

- [ ] **Step 12: Commit**

```bash
cd /home/hieuly/hieuly/project/LLM-SRE
git add backend/app/interface/http backend/tests/test_incidents_http.py backend/tests/test_incident_chat_http.py
git commit -m "feat: add incident chat HTTP endpoints, remove log-search endpoint"
```

---

### Task 7: Frontend — `ChatPanel`, remove `LogSearchPanel`

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Create: `frontend/src/features/incidents/ChatPanel.tsx`
- Modify: `frontend/src/features/incidents/IncidentDetail.tsx`

**Interfaces:**
- Consumes: `ChatMessageOut` (new type, this task), `GET/POST /api/incidents/{id}/chat` (Task 6).
- Produces: `<ChatPanel incident={detail} />` component.

- [ ] **Step 1: Update `types.ts`**

In `frontend/src/lib/types.ts`, delete the `LogEventOut` and `LogSearchResult` interfaces
entirely, and add this in their place (same location):

```typescript
export interface ChatMessageOut {
  id: string
  role: 'user' | 'assistant'
  content: string
  input_tokens: number | null
  output_tokens: number | null
  created_at: string
}
```

- [ ] **Step 2: Write `ChatPanel.tsx`**

Create `frontend/src/features/incidents/ChatPanel.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { MessageSquare, Send } from 'lucide-react'
import { api, errText } from '../../lib/api'
import type { ChatMessageOut, IncidentDetail } from '../../lib/types'
import { Card } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'

export function ChatPanel({ incident }: { incident: IncidentDetail }) {
  const [messages, setMessages] = useState<ChatMessageOut[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setMessages([])
    setErr(null)
    setLoading(true)
    api
      .get<ChatMessageOut[]>(`/api/incidents/${incident.id}/chat`)
      .then(setMessages)
      .catch((e) => setErr(errText(e)))
      .finally(() => setLoading(false))
  }, [incident.id])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = (e: React.FormEvent) => {
    e.preventDefault()
    const text = input.trim()
    if (!text || sending) return
    setSending(true)
    setErr(null)
    const optimisticUser: ChatMessageOut = {
      id: `pending-${Date.now()}`,
      role: 'user',
      content: text,
      input_tokens: null,
      output_tokens: null,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, optimisticUser])
    setInput('')
    api
      .post<ChatMessageOut>(`/api/incidents/${incident.id}/chat`, { message: text })
      .then((reply) => setMessages((prev) => [...prev, reply]))
      .catch((e) => setErr(errText(e)))
      .finally(() => setSending(false))
  }

  return (
    <Card className="p-5">
      <div className="flex items-center gap-2">
        <MessageSquare size={15} className="text-accent" />
        <h3 className="font-display text-sm font-bold text-ink">Chat with Claude</h3>
      </div>

      <div className="mt-3 max-h-96 space-y-3 overflow-y-auto">
        {loading && <p className="text-sm text-muted">Loading…</p>}
        {!loading && messages.length === 0 && (
          <p className="text-sm text-muted">
            Ask about this incident — e.g. "check logs of that service around this time".
          </p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-relaxed ${
                m.role === 'user' ? 'bg-accent text-white' : 'bg-surface-2 text-ink'
              }`}
            >
              <p className="whitespace-pre-wrap">{m.content}</p>
              {m.role === 'assistant' && m.input_tokens !== null && m.output_tokens !== null && (
                <p className="mt-1 font-mono text-[10px] opacity-70">
                  {m.input_tokens.toLocaleString()} in / {m.output_tokens.toLocaleString()} out
                </p>
              )}
            </div>
          </div>
        ))}
        {sending && <p className="text-xs text-muted">Claude is thinking…</p>}
        <div ref={bottomRef} />
      </div>

      {err && <p className="mt-2 text-sm text-sev-critical">{err}</p>}

      <form onSubmit={send} className="mt-3 flex items-center gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about this incident…"
          disabled={sending}
          className="flex-1 rounded-xl border border-hair bg-surface-2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
        />
        <Button type="submit" disabled={sending || !input.trim()}>
          <Send size={15} /> Send
        </Button>
      </form>
    </Card>
  )
}
```

- [ ] **Step 3: Wire it into `IncidentDetail.tsx`, remove `LogSearchPanel`**

In `frontend/src/features/incidents/IncidentDetail.tsx`:

Change the icon import (remove `Search`, it's no longer used anywhere in this file once
`LogSearchPanel` is deleted):

```tsx
import {
  AlertTriangle,
  CheckCircle2,
  Coins,
  Cpu,
  FileSearch,
  FileText,
  Gauge,
  MousePointerClick,
  Sparkles,
  Ticket,
} from 'lucide-react'
```

Change the type import (remove `LogSearchResult`, add nothing — `ChatPanel` imports its own
`ChatMessageOut`):

```tsx
import type { IncidentCreated, IncidentDetail as Detail } from '../../lib/types'
```

Add a new import for `ChatPanel` (alongside the other same-directory imports — there are none
currently since this file historically defined its own sub-components inline, so add it near the
top with the other imports):

```tsx
import { ChatPanel } from './ChatPanel'
```

Delete the `guessLogGroup` and `toLocalInputValue` helper functions entirely (lines 31-41 in the
current file — both are only used by `LogSearchPanel`, which this step also deletes).

Change:

```tsx
      {/* CloudWatch log search */}
      <LogSearchPanel incident={d} onSearched={(next) => setD(next)} />
```

to:

```tsx
      {/* Chat */}
      <ChatPanel incident={d} />
```

Delete the entire `LogSearchPanel` function (from `function LogSearchPanel({` through its closing
`}` right before `function DetailSkeleton()`).

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: clean build, no TypeScript errors (confirms no remaining references to
`LogSearchResult`/`LogEventOut`/`guessLogGroup`/`toLocalInputValue`/the `Search` icon import).

- [ ] **Step 5: Manual smoke test**

Run: `docker compose up -d --build backend frontend` from the repo root, then open
`http://localhost:5173`, open any incident, and:
1. Confirm the "Search logs" panel is gone and a "Chat with Claude" panel appears in its place.
2. Type a question and send it; confirm a "Claude is thinking…" state appears, then a reply
   renders.
3. Reload the page; confirm the prior messages are still there (persisted, not lost).
4. Ask something that requires a log lookup (e.g. "check the logs for this service around the
   incident time") and confirm the reply reflects actual fetched log content, not a generic
   "I don't have access to logs" answer — this is the end-to-end proof that `--mcp-config` /
   `--allowedTools` / the `mcp_log_tool.py` subprocess are wired correctly. If `DEMO_LOGS` isn't
   set for this environment, this step needs a real CloudWatch-backed connection/service to be
   meaningful; note in your task report which mode you actually verified against.

- [ ] **Step 6: Commit**

```bash
cd /home/hieuly/hieuly/project/LLM-SRE
git add frontend/src/lib/types.ts frontend/src/features/incidents/ChatPanel.tsx frontend/src/features/incidents/IncidentDetail.tsx
git commit -m "feat: replace log-search panel with incident chat"
```
