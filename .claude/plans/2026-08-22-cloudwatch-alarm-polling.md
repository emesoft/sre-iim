# CloudWatch Alarm Polling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Poll CloudWatch Alarms across configured AWS accounts hourly (plus a manual trigger), auto-creating an incident when an alarm fires and auto-resolving it when the alarm recovers, with per-account credentials (SSO profile or encrypted access key) manageable from a new Settings page.

**Architecture:** Follows the existing hexagonal layering (`domain` → `application` → `infrastructure` → `interface`). Two new tables (`cloud_connections`, `tracked_alarms`) back a new `CloudConnection`/`TrackedAlarm` domain slice. A `PollAlarmsJob` application use case runs the ALARM↔OK state machine and drives the *existing* `IngestIncident`/`ResolveIncident` use cases unchanged — alarms become incidents through the same door manual/demo incidents already use (`source="cloudwatch_alarm"`, which the `source` column and `<Badge>{d.source}</Badge>` in `IncidentDetail.tsx` already render with no frontend change needed). The job is invoked by an APScheduler interval trigger (60 min) and by a manual `POST /api/cloud-connections/poll` endpoint — same code, two triggers.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + Alembic, Postgres/pgvector, boto3, `cryptography` (Fernet), `apscheduler`, React + TypeScript (Vite).

**Spec:** `.claude/specs/2026-08-22-cloudwatch-alarm-polling-design.md`

## Global Constraints

- Poll interval: **60 minutes** (not 5 — demo-friendly, low API/cost footprint). Verbatim from spec.
- v1 polls **every** alarm in a connected account — no tag/prefix filtering.
- Secrets encrypted with a Fernet key from `SECRET_ENCRYPTION_KEY` (env var, never in DB) — no KMS/Secrets Manager.
- `cloud_connections.project` is free text with no DB constraint — the UI offers known divisions (BEC/EVP/GCM/SmartSuite/IIM) plus a free-entry option.
- API responses for connections never include the encrypted secret fields at all (not even masked) — the DTO simply omits them, exposing only `has_access_key: bool`.
- Per-connection poll failures must not stop other connections' polls (error isolation), recorded on `last_poll_status`/`last_poll_error`.
- No alarm filtering, no flapping suppression, no Azure/GCP — out of scope per spec, do not add.

---

## Task 1: Dependencies + settings

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/infrastructure/config.py`
- Modify: `backend/env.example`

**Interfaces:**
- Produces: `Settings.secret_encryption_key: str`, `Settings.alarm_poll_interval_minutes: int` (used by Task 15's scheduler and Task 6's `Encryptor`).

- [ ] **Step 1: Add dependencies**

In `backend/pyproject.toml`, add to the `dependencies` list (boto3 is already present):

```toml
    "cryptography>=42.0",
    "apscheduler>=3.10",
```

- [ ] **Step 2: Install and verify**

Run: `cd backend && uv sync`
Expected: resolves and installs `cryptography` and `apscheduler` with no errors.

- [ ] **Step 3: Add settings fields**

In `backend/app/infrastructure/config.py`, add after the `# --- Ticketing (Azure DevOps) ---` block:

```python
    # --- Cloud connections (CloudWatch alarm polling) ---
    secret_encryption_key: str = ""  # Fernet key (44-char urlsafe base64); required to store access keys
    alarm_poll_interval_minutes: int = 60
```

- [ ] **Step 4: Document the new env var**

In `backend/env.example`, add near the AWS section:

```bash
# Fernet key for encrypting cloud-connection access keys at rest. Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SECRET_ENCRYPTION_KEY=
```

- [ ] **Step 5: Commit**

```bash
cd /home/hieuly/hieuly/project/LLM-SRE
git add backend/pyproject.toml backend/uv.lock backend/app/infrastructure/config.py backend/env.example
git commit -m "chore: add cryptography/apscheduler deps and cloud-connection settings"
```

---

## Task 2: Migration — `cloud_connections` and `tracked_alarms` tables

**Files:**
- Create: `backend/migrations/versions/0006_cloud_connections.py`

**Interfaces:**
- Produces: tables `cloud_connections` and `tracked_alarms` (columns exactly as in the spec's Data Model section), consumed by Task 3's ORM rows.

- [ ] **Step 1: Write the migration**

```python
"""add cloud_connections and tracked_alarms

Revision ID: 0006_cloud_connections
Revises: 0005_incident_ticket_url
Create Date: 2026-08-22

CloudWatch alarm polling: per-account connection config (SSO profile or encrypted access key) and
the last-seen state of each polled alarm, so the poller can tell OK->ALARM from ALARM->ALARM.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_cloud_connections"
down_revision: Union[str, None] = "0005_incident_ticket_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cloud_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("env", sa.Text(), nullable=False),
        sa.Column("cloud", sa.Text(), nullable=False, server_default="aws"),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.Text(), nullable=False),  # sso | access_key
        sa.Column("sso_profile_name", sa.Text(), nullable=True),
        sa.Column("encrypted_access_key_id", sa.Text(), nullable=True),
        sa.Column("encrypted_secret_access_key", sa.Text(), nullable=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_poll_status", sa.Text(), nullable=True),  # ok | error
        sa.Column("last_poll_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "tracked_alarms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cloud_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alarm_arn", sa.Text(), nullable=False),
        sa.Column("alarm_name", sa.Text(), nullable=False),
        sa.Column("last_state", sa.Text(), nullable=False),  # OK | ALARM
        sa.Column(
            "incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id"), nullable=True
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_tracked_alarms_connection_id", "tracked_alarms", ["connection_id"])
    op.create_unique_constraint(
        "uq_tracked_alarms_connection_arn", "tracked_alarms", ["connection_id", "alarm_arn"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_tracked_alarms_connection_arn", "tracked_alarms", type_="unique")
    op.drop_index("ix_tracked_alarms_connection_id", table_name="tracked_alarms")
    op.drop_table("tracked_alarms")
    op.drop_table("cloud_connections")
```

- [ ] **Step 2: Run the migration against the local DB**

Run: `cd backend && docker compose -f ../docker-compose.yml up -d db && uv run alembic upgrade head`
Expected: `Running upgrade 0005_incident_ticket_url -> 0006_cloud_connections, add cloud_connections and tracked_alarms`, no errors.

- [ ] **Step 3: Verify downgrade works**

Run: `cd backend && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: both commands succeed with no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/migrations/versions/0006_cloud_connections.py
git commit -m "feat: add cloud_connections and tracked_alarms tables"
```

---

## Task 3: ORM rows

**Files:**
- Modify: `backend/app/infrastructure/db/orm.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `CloudConnectionRow`, `TrackedAlarmRow` (SQLAlchemy models), consumed by Task 7's repositories.

- [ ] **Step 1: Add the ORM classes**

Append to `backend/app/infrastructure/db/orm.py`:

```python
class CloudConnectionRow(Base):
    __tablename__ = "cloud_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project: Mapped[str] = mapped_column(Text, nullable=False)
    env: Mapped[str] = mapped_column(Text, nullable=False)
    cloud: Mapped[str] = mapped_column(Text, nullable=False, default="aws")
    region: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(Text, nullable=False)  # sso | access_key
    sso_profile_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_access_key_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_secret_access_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_poll_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _utcnow_column()


class TrackedAlarmRow(Base):
    __tablename__ = "tracked_alarms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cloud_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alarm_arn: Mapped[str] = mapped_column(Text, nullable=False)
    alarm_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_state: Mapped[str] = mapped_column(Text, nullable=False)  # OK | ALARM
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True
    )
    updated_at: Mapped[datetime] = _utcnow_column()
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd backend && uv run python -c "from app.infrastructure.db import orm; print(orm.CloudConnectionRow.__tablename__, orm.TrackedAlarmRow.__tablename__)"`
Expected: `cloud_connections tracked_alarms`

- [ ] **Step 3: Commit**

```bash
git add backend/app/infrastructure/db/orm.py
git commit -m "feat: add CloudConnectionRow and TrackedAlarmRow ORM models"
```

---

## Task 4: Domain entities

**Files:**
- Create: `backend/app/domain/cloud_connections/__init__.py` (empty)
- Create: `backend/app/domain/cloud_connections/entities.py`

**Interfaces:**
- Produces: `CloudConnection`, `TrackedAlarm`, `AlarmState` dataclasses — consumed by every later task in this plan.

- [ ] **Step 1: Write the entities**

```python
"""Domain entities for cloud connections (per-account AWS credentials) and alarm polling state.

Plain dataclasses, no ORM/framework coupling — same convention as domain/incidents/entities.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CloudConnection:
    """One AWS account IIM watches for CloudWatch alarms."""

    project: str
    env: str
    region: str
    auth_type: str  # sso | access_key
    cloud: str = "aws"
    sso_profile_name: str | None = None
    encrypted_access_key_id: str | None = None
    encrypted_secret_access_key: str | None = None
    last_poll_at: datetime | None = None
    last_poll_status: str | None = None  # ok | error
    last_poll_error: str | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None


@dataclass
class TrackedAlarm:
    """Last-seen state of one CloudWatch alarm for one connection, and the incident it opened
    (if any), so the poller can tell OK->ALARM from an alarm that's already open."""

    connection_id: uuid.UUID
    alarm_arn: str
    alarm_name: str
    last_state: str  # OK | ALARM
    incident_id: uuid.UUID | None = None
    id: uuid.UUID | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AlarmState:
    """One alarm's current state, as read from CloudWatch `describe_alarms` for this poll cycle."""

    arn: str
    name: str
    state: str  # OK | ALARM | INSUFFICIENT_DATA
    reason: str | None = None
    metric_name: str | None = None
    namespace: str | None = None
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd backend && uv run python -c "from app.domain.cloud_connections.entities import CloudConnection, TrackedAlarm, AlarmState; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/domain/cloud_connections/
git commit -m "feat: add CloudConnection/TrackedAlarm/AlarmState domain entities"
```

---

## Task 5: Domain ports

**Files:**
- Create: `backend/app/domain/cloud_connections/ports.py`

**Interfaces:**
- Consumes: `CloudConnection`, `TrackedAlarm`, `AlarmState` (Task 4).
- Produces: `CloudConnectionRepository`, `TrackedAlarmRepository`, `AlarmFetcher` protocols — implemented by Task 7 (repos) and Task 9 (fetcher), consumed by Task 10 (`ManageCloudConnections`) and Task 11 (`PollAlarmsJob`).

- [ ] **Step 1: Write the ports**

```python
"""Ports the cloud-connection use cases depend on. Implemented in the infrastructure layer.

Same dependency-inversion convention as domain/incidents/ports.py.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.domain.cloud_connections.entities import AlarmState, CloudConnection, TrackedAlarm

__all__ = ["CloudConnectionRepository", "TrackedAlarmRepository", "AlarmFetcher"]


class CloudConnectionRepository(Protocol):
    """Persistence for cloud connections."""

    async def add(self, connection: CloudConnection) -> CloudConnection: ...

    async def get(self, connection_id: uuid.UUID) -> CloudConnection | None: ...

    async def list(self) -> list[CloudConnection]: ...

    async def delete(self, connection_id: uuid.UUID) -> None: ...

    async def record_poll_result(
        self, connection_id: uuid.UUID, *, status: str, error: str | None
    ) -> None:
        """Update last_poll_at (now)/last_poll_status/last_poll_error after a poll attempt."""
        ...


class TrackedAlarmRepository(Protocol):
    """Persistence for per-alarm polling state."""

    async def get(self, connection_id: uuid.UUID, alarm_arn: str) -> TrackedAlarm | None: ...

    async def upsert(
        self,
        connection_id: uuid.UUID,
        *,
        alarm_arn: str,
        alarm_name: str,
        last_state: str,
        incident_id: uuid.UUID | None,
    ) -> None: ...


class AlarmFetcher(Protocol):
    """Reads the current alarm states for one connection's AWS account."""

    async def list_alarms(self, connection: CloudConnection) -> list[AlarmState]: ...
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd backend && uv run python -c "from app.domain.cloud_connections.ports import CloudConnectionRepository, TrackedAlarmRepository, AlarmFetcher; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/domain/cloud_connections/ports.py
git commit -m "feat: add cloud-connection domain ports"
```

---

## Task 6: Encryptor

**Files:**
- Create: `backend/app/infrastructure/security/__init__.py` (empty)
- Create: `backend/app/infrastructure/security/encryptor.py`
- Test: `backend/tests/test_encryptor.py`

**Interfaces:**
- Produces: `Encryptor` class with `encrypt(plaintext: str) -> str` / `decrypt(ciphertext: str) -> str`, constructed as `Encryptor(key: str)`. Consumed by Task 8 (`CredentialResolver`) and Task 10 (`ManageCloudConnections`).

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for Encryptor — no DB, no network."""

import pytest

from app.infrastructure.security.encryptor import Encryptor

_KEY = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="  # a valid Fernet key for tests


def test_round_trip():
    enc = Encryptor(_KEY)
    ciphertext = enc.encrypt("AKIAEXAMPLE")
    assert ciphertext != "AKIAEXAMPLE"
    assert enc.decrypt(ciphertext) == "AKIAEXAMPLE"


def test_missing_key_raises():
    with pytest.raises(ValueError, match="SECRET_ENCRYPTION_KEY"):
        Encryptor("")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_encryptor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.infrastructure.security'`

- [ ] **Step 3: Write the implementation**

```python
"""Symmetric encryption for cloud-connection access keys at rest (Fernet, app-level key).

Not a managed secret store (no KMS/Secrets Manager) — deliberate scope decision, see the design
spec's "Non-goals". The key comes from `SECRET_ENCRYPTION_KEY` and must never be stored in the DB.
"""

from __future__ import annotations

from cryptography.fernet import Fernet


class Encryptor:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("SECRET_ENCRYPTION_KEY is not set")
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_encryptor.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/infrastructure/security/ backend/tests/test_encryptor.py
git commit -m "feat: add Fernet-based Encryptor for cloud-connection secrets"
```

---

## Task 7: SQLAlchemy repositories

**Files:**
- Create: `backend/app/infrastructure/db/repositories/cloud_connections.py`
- Modify: `backend/app/infrastructure/db/repositories/__init__.py`
- Modify: `backend/app/infrastructure/db/repositories/mappers.py`

**Interfaces:**
- Consumes: `CloudConnectionRow`, `TrackedAlarmRow` (Task 3), `CloudConnection`, `TrackedAlarm` (Task 4).
- Produces: `SqlAlchemyCloudConnectionRepository`, `SqlAlchemyTrackedAlarmRepository` implementing Task 5's ports — consumed by Task 14 (deps.py wiring).

- [ ] **Step 1: Add domain<->row mappers**

Append to `backend/app/infrastructure/db/repositories/mappers.py`:

```python
def cloud_connection_to_domain(row) -> CloudConnection:
    return CloudConnection(
        id=row.id,
        project=row.project,
        env=row.env,
        cloud=row.cloud,
        region=row.region,
        auth_type=row.auth_type,
        sso_profile_name=row.sso_profile_name,
        encrypted_access_key_id=row.encrypted_access_key_id,
        encrypted_secret_access_key=row.encrypted_secret_access_key,
        last_poll_at=row.last_poll_at,
        last_poll_status=row.last_poll_status,
        last_poll_error=row.last_poll_error,
        created_at=row.created_at,
    )


def tracked_alarm_to_domain(row) -> TrackedAlarm:
    return TrackedAlarm(
        id=row.id,
        connection_id=row.connection_id,
        alarm_arn=row.alarm_arn,
        alarm_name=row.alarm_name,
        last_state=row.last_state,
        incident_id=row.incident_id,
        updated_at=row.updated_at,
    )
```

Add the two new imports at the top of `mappers.py` alongside the existing ones:

```python
from app.domain.cloud_connections.entities import CloudConnection, TrackedAlarm
```

- [ ] **Step 2: Write the repositories**

```python
"""SQLAlchemy repositories for cloud connections and tracked alarms (implements the ports in
domain/cloud_connections/ports.py). Same convention as infrastructure/db/repositories/incidents.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cloud_connections.entities import CloudConnection, TrackedAlarm
from app.infrastructure.db.orm import CloudConnectionRow, TrackedAlarmRow
from app.infrastructure.db.repositories.mappers import (
    cloud_connection_to_domain,
    tracked_alarm_to_domain,
)


class SqlAlchemyCloudConnectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, connection: CloudConnection) -> CloudConnection:
        row = CloudConnectionRow(
            project=connection.project,
            env=connection.env,
            cloud=connection.cloud,
            region=connection.region,
            auth_type=connection.auth_type,
            sso_profile_name=connection.sso_profile_name,
            encrypted_access_key_id=connection.encrypted_access_key_id,
            encrypted_secret_access_key=connection.encrypted_secret_access_key,
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return cloud_connection_to_domain(row)

    async def get(self, connection_id: uuid.UUID) -> CloudConnection | None:
        row = await self._s.get(CloudConnectionRow, connection_id)
        return cloud_connection_to_domain(row) if row else None

    async def list(self) -> list[CloudConnection]:
        rows = (await self._s.execute(select(CloudConnectionRow))).scalars().all()
        return [cloud_connection_to_domain(row) for row in rows]

    async def delete(self, connection_id: uuid.UUID) -> None:
        row = await self._s.get(CloudConnectionRow, connection_id)
        if row is not None:
            await self._s.delete(row)
            await self._s.flush()

    async def record_poll_result(
        self, connection_id: uuid.UUID, *, status: str, error: str | None
    ) -> None:
        row = await self._s.get(CloudConnectionRow, connection_id)
        if row is None:
            return
        row.last_poll_at = datetime.now(timezone.utc)
        row.last_poll_status = status
        row.last_poll_error = error
        await self._s.flush()


class SqlAlchemyTrackedAlarmRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, connection_id: uuid.UUID, alarm_arn: str) -> TrackedAlarm | None:
        stmt = select(TrackedAlarmRow).where(
            TrackedAlarmRow.connection_id == connection_id, TrackedAlarmRow.alarm_arn == alarm_arn
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return tracked_alarm_to_domain(row) if row else None

    async def upsert(
        self,
        connection_id: uuid.UUID,
        *,
        alarm_arn: str,
        alarm_name: str,
        last_state: str,
        incident_id: uuid.UUID | None,
    ) -> None:
        stmt = select(TrackedAlarmRow).where(
            TrackedAlarmRow.connection_id == connection_id, TrackedAlarmRow.alarm_arn == alarm_arn
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = TrackedAlarmRow(connection_id=connection_id, alarm_arn=alarm_arn)
            self._s.add(row)
        row.alarm_name = alarm_name
        row.last_state = last_state
        row.incident_id = incident_id
        await self._s.flush()
```

- [ ] **Step 3: Export from the repositories package**

In `backend/app/infrastructure/db/repositories/__init__.py`, add:

```python
from app.infrastructure.db.repositories.cloud_connections import (
    SqlAlchemyCloudConnectionRepository,
    SqlAlchemyTrackedAlarmRepository,
)
```

and add both names to that module's `__all__` list.

- [ ] **Step 4: Verify it imports cleanly**

Run: `cd backend && uv run python -c "from app.infrastructure.db.repositories import SqlAlchemyCloudConnectionRepository, SqlAlchemyTrackedAlarmRepository; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add backend/app/infrastructure/db/repositories/
git commit -m "feat: add SQLAlchemy repositories for cloud connections and tracked alarms"
```

---

## Task 8: CredentialResolver

**Files:**
- Create: `backend/app/infrastructure/cloud/__init__.py` (empty)
- Create: `backend/app/infrastructure/cloud/credential_resolver.py`
- Test: `backend/tests/test_credential_resolver.py`

**Interfaces:**
- Consumes: `CloudConnection` (Task 4), `Encryptor` (Task 6).
- Produces: `CredentialResolver` with `resolve(connection: CloudConnection) -> boto3.Session` — consumed by Task 9 (`CloudWatchAlarmFetcher`).

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for CredentialResolver — verifies which boto3.Session kwargs each auth_type builds,
without making a real AWS call."""

from unittest.mock import patch

import pytest

from app.domain.cloud_connections.entities import CloudConnection
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.security.encryptor import Encryptor

_KEY = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="


def test_sso_connection_uses_profile_name():
    connection = CloudConnection(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="sso",
        sso_profile_name="GCM-Prod-ReadOnlyAccess",
    )
    resolver = CredentialResolver(Encryptor(_KEY))
    with patch("app.infrastructure.cloud.credential_resolver.boto3.Session") as mock_session:
        resolver.resolve(connection)
    mock_session.assert_called_once_with(
        profile_name="GCM-Prod-ReadOnlyAccess", region_name="ap-southeast-1"
    )


def test_access_key_connection_decrypts_and_builds_session():
    encryptor = Encryptor(_KEY)
    connection = CloudConnection(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="access_key",
        encrypted_access_key_id=encryptor.encrypt("AKIAEXAMPLE"),
        encrypted_secret_access_key=encryptor.encrypt("supersecret"),
    )
    resolver = CredentialResolver(encryptor)
    with patch("app.infrastructure.cloud.credential_resolver.boto3.Session") as mock_session:
        resolver.resolve(connection)
    mock_session.assert_called_once_with(
        aws_access_key_id="AKIAEXAMPLE",
        aws_secret_access_key="supersecret",
        region_name="ap-southeast-1",
    )


def test_unknown_auth_type_raises():
    connection = CloudConnection(project="GCM", env="prod", region="us-east-1", auth_type="bogus")
    resolver = CredentialResolver(Encryptor(_KEY))
    with pytest.raises(ValueError, match="auth_type"):
        resolver.resolve(connection)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_credential_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.infrastructure.cloud'`

- [ ] **Step 3: Write the implementation**

```python
"""Builds a boto3.Session for a CloudConnection — SSO reads the host's mounted ~/.aws profile
cache, access_key decrypts the stored key pair. See design spec "Credentials & security"."""

from __future__ import annotations

import boto3

from app.domain.cloud_connections.entities import CloudConnection
from app.infrastructure.security.encryptor import Encryptor


class CredentialResolver:
    def __init__(self, encryptor: Encryptor) -> None:
        self._encryptor = encryptor

    def resolve(self, connection: CloudConnection) -> boto3.Session:
        if connection.auth_type == "sso":
            return boto3.Session(
                profile_name=connection.sso_profile_name, region_name=connection.region
            )
        if connection.auth_type == "access_key":
            return boto3.Session(
                aws_access_key_id=self._encryptor.decrypt(connection.encrypted_access_key_id),
                aws_secret_access_key=self._encryptor.decrypt(connection.encrypted_secret_access_key),
                region_name=connection.region,
            )
        raise ValueError(f"unknown auth_type: {connection.auth_type!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_credential_resolver.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/infrastructure/cloud/credential_resolver.py backend/tests/test_credential_resolver.py
git commit -m "feat: add CredentialResolver for SSO/access-key cloud connections"
```

---

## Task 9: CloudWatchAlarmFetcher

**Files:**
- Create: `backend/app/infrastructure/cloud/cloudwatch_alarms.py`
- Test: `backend/tests/test_cloudwatch_alarm_fetcher.py`

**Interfaces:**
- Consumes: `CredentialResolver` (Task 8), `AlarmState` (Task 4), `AlarmFetcher` port (Task 5).
- Produces: `CloudWatchAlarmFetcher` implementing `AlarmFetcher.list_alarms` — consumed by Task 11 (`PollAlarmsJob`) and Task 14 (deps.py wiring).

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for CloudWatchAlarmFetcher — fakes the boto3 CloudWatch client, no real AWS call."""

import pytest

from app.domain.cloud_connections.entities import CloudConnection
from app.infrastructure.cloud.cloudwatch_alarms import CloudWatchAlarmFetcher

pytestmark = pytest.mark.asyncio

_CONNECTION = CloudConnection(
    project="GCM", env="prod", region="ap-southeast-1", auth_type="sso", sso_profile_name="p"
)


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self):
        return iter(self._pages)


class _FakeClient:
    def __init__(self, pages):
        self._pages = pages

    def get_paginator(self, name):
        assert name == "describe_alarms"
        return _FakePaginator(self._pages)


class _FakeSession:
    def __init__(self, pages):
        self._pages = pages

    def client(self, service_name, region_name=None):
        assert service_name == "cloudwatch"
        return _FakeClient(self._pages)


class _FakeResolver:
    def __init__(self, session):
        self._session = session

    def resolve(self, connection):
        return self._session


async def test_lists_alarms_across_pages():
    pages = [
        {"MetricAlarms": [{"AlarmArn": "arn:1", "AlarmName": "cpu-high", "StateValue": "ALARM",
                           "StateReason": "high cpu", "MetricName": "CPUUtilization", "Namespace": "AWS/ECS"}]},
        {"MetricAlarms": [{"AlarmArn": "arn:2", "AlarmName": "mem-high", "StateValue": "OK"}]},
    ]
    fetcher = CloudWatchAlarmFetcher(_FakeResolver(_FakeSession(pages)))
    alarms = await fetcher.list_alarms(_CONNECTION)
    assert [a.arn for a in alarms] == ["arn:1", "arn:2"]
    assert alarms[0].state == "ALARM"
    assert alarms[0].reason == "high cpu"
    assert alarms[1].state == "OK"
    assert alarms[1].reason is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_cloudwatch_alarm_fetcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.infrastructure.cloud.cloudwatch_alarms'`

- [ ] **Step 3: Write the implementation**

```python
"""Reads CloudWatch alarm states for one connection via boto3 (sync SDK, run in a thread —
same pattern as infrastructure/llm/chat.py's BedrockChatModel)."""

from __future__ import annotations

import asyncio

from app.domain.cloud_connections.entities import AlarmState, CloudConnection
from app.infrastructure.cloud.credential_resolver import CredentialResolver


class CloudWatchAlarmFetcher:
    def __init__(self, resolver: CredentialResolver) -> None:
        self._resolver = resolver

    async def list_alarms(self, connection: CloudConnection) -> list[AlarmState]:
        def _fetch() -> list[AlarmState]:
            session = self._resolver.resolve(connection)
            client = session.client("cloudwatch", region_name=connection.region)
            alarms: list[AlarmState] = []
            for page in client.get_paginator("describe_alarms").paginate():
                for a in page.get("MetricAlarms", []):
                    alarms.append(
                        AlarmState(
                            arn=a["AlarmArn"],
                            name=a["AlarmName"],
                            state=a["StateValue"],
                            reason=a.get("StateReason"),
                            metric_name=a.get("MetricName"),
                            namespace=a.get("Namespace"),
                        )
                    )
            return alarms

        return await asyncio.to_thread(_fetch)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_cloudwatch_alarm_fetcher.py -v`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/infrastructure/cloud/cloudwatch_alarms.py backend/tests/test_cloudwatch_alarm_fetcher.py
git commit -m "feat: add CloudWatchAlarmFetcher"
```

---

## Task 10: PollAlarmsJob (the ALARM/OK state machine)

**Files:**
- Create: `backend/app/application/cloud_connections/__init__.py` (empty)
- Create: `backend/app/application/cloud_connections/poll_alarms.py`
- Test: `backend/tests/test_poll_alarms_job.py`

**Interfaces:**
- Consumes: `CloudConnectionRepository`, `TrackedAlarmRepository`, `AlarmFetcher` (Task 5), `IngestIncident.execute(*, source, context) -> tuple[Incident, Analysis]` and `ResolveIncident.resolve(incident_id, *, resolution_notes) -> Incident` (existing, unchanged), `UnitOfWork.commit()` (existing).
- Produces: `PollAlarmsJob.run() -> None` — consumed by Task 13 (HTTP `/poll` endpoints) and Task 15 (scheduler).

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for PollAlarmsJob: the ALARM<->OK state machine, using in-memory fakes.

Covers: OK/untracked -> ALARM creates an incident; ALARM -> ALARM is a no-op (no duplicate
incident); ALARM -> OK auto-resolves; one connection's fetch error doesn't stop the others.
"""

import uuid

import pytest

from app.application.cloud_connections.poll_alarms import PollAlarmsJob
from app.domain.cloud_connections.entities import AlarmState, CloudConnection, TrackedAlarm

pytestmark = pytest.mark.asyncio


class FakeConnectionRepo:
    def __init__(self, connections):
        self._connections = {c.id: c for c in connections}
        self.poll_results = []

    async def list(self):
        return list(self._connections.values())

    async def record_poll_result(self, connection_id, *, status, error):
        self.poll_results.append((connection_id, status, error))


class FakeTrackedAlarmRepo:
    def __init__(self, seed=None):
        self._rows: dict[tuple, TrackedAlarm] = seed or {}

    async def get(self, connection_id, alarm_arn):
        return self._rows.get((connection_id, alarm_arn))

    async def upsert(self, connection_id, *, alarm_arn, alarm_name, last_state, incident_id):
        self._rows[(connection_id, alarm_arn)] = TrackedAlarm(
            connection_id=connection_id, alarm_arn=alarm_arn, alarm_name=alarm_name,
            last_state=last_state, incident_id=incident_id,
        )


class FakeFetcher:
    def __init__(self, by_connection):
        self._by_connection = by_connection

    async def list_alarms(self, connection):
        result = self._by_connection[connection.id]
        if isinstance(result, Exception):
            raise result
        return result


class FakeIngest:
    def __init__(self):
        self.calls = []

    async def execute(self, *, source, context):
        self.calls.append((source, context))
        incident = type("I", (), {"id": uuid.uuid4()})()
        return incident, None


class FakeResolve:
    def __init__(self):
        self.calls = []

    async def resolve(self, incident_id, *, resolution_notes):
        self.calls.append((incident_id, resolution_notes))


class FakeUnitOfWork:
    async def commit(self):
        pass


def _connection(**kw):
    return CloudConnection(
        id=uuid.uuid4(), project="GCM", env="prod", region="ap-southeast-1", auth_type="sso",
        sso_profile_name="p", **kw,
    )


async def test_new_alarm_creates_an_incident():
    conn = _connection()
    fetcher = FakeFetcher({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")]})
    tracked = FakeTrackedAlarmRepo()
    ingest = FakeIngest()
    job = PollAlarmsJob(
        connections=FakeConnectionRepo([conn]), tracked=tracked, fetcher=fetcher,
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    assert len(ingest.calls) == 1
    assert ingest.calls[0][0] == "cloudwatch_alarm"
    assert (await tracked.get(conn.id, "arn:1")).last_state == "ALARM"


async def test_already_alarming_does_not_create_a_second_incident():
    conn = _connection()
    existing_incident_id = uuid.uuid4()
    tracked = FakeTrackedAlarmRepo(
        seed={(conn.id, "arn:1"): TrackedAlarm(
            connection_id=conn.id, alarm_arn="arn:1", alarm_name="cpu-high",
            last_state="ALARM", incident_id=existing_incident_id,
        )}
    )
    fetcher = FakeFetcher({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")]})
    ingest = FakeIngest()
    job = PollAlarmsJob(
        connections=FakeConnectionRepo([conn]), tracked=tracked, fetcher=fetcher,
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    assert ingest.calls == []


async def test_recovered_alarm_auto_resolves_the_incident():
    conn = _connection()
    incident_id = uuid.uuid4()
    tracked = FakeTrackedAlarmRepo(
        seed={(conn.id, "arn:1"): TrackedAlarm(
            connection_id=conn.id, alarm_arn="arn:1", alarm_name="cpu-high",
            last_state="ALARM", incident_id=incident_id,
        )}
    )
    fetcher = FakeFetcher({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="OK")]})
    resolve = FakeResolve()
    job = PollAlarmsJob(
        connections=FakeConnectionRepo([conn]), tracked=tracked, fetcher=fetcher,
        ingest=FakeIngest(), resolve=resolve, uow=FakeUnitOfWork(),
    )
    await job.run()
    assert resolve.calls == [(incident_id, resolve.calls[0][1])]
    assert (await tracked.get(conn.id, "arn:1")).last_state == "OK"
    assert (await tracked.get(conn.id, "arn:1")).incident_id is None


async def test_one_connection_error_does_not_stop_the_others():
    good = _connection()
    bad = _connection()
    fetcher = FakeFetcher({
        good.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")],
        bad.id: RuntimeError("SSO token expired"),
    })
    connections = FakeConnectionRepo([bad, good])
    ingest = FakeIngest()
    job = PollAlarmsJob(
        connections=connections, tracked=FakeTrackedAlarmRepo(), fetcher=fetcher,
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    assert len(ingest.calls) == 1  # good connection still processed
    assert (bad.id, "error", "SSO token expired") in connections.poll_results
    assert any(cid == good.id and status == "ok" for cid, status, _ in connections.poll_results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_poll_alarms_job.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.application.cloud_connections'`

- [ ] **Step 3: Write the implementation**

```python
"""PollAlarmsJob: the ALARM<->OK state machine that turns CloudWatch alarms into incidents.

Drives the existing IngestIncident/ResolveIncident use cases unchanged (source="cloudwatch_alarm").
Per-connection failures are isolated so one bad connection (expired SSO token, revoked key) doesn't
block the others — see design spec "Polling & state machine".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.cloud_connections.entities import AlarmState, CloudConnection
from app.domain.cloud_connections.ports import (
    AlarmFetcher,
    CloudConnectionRepository,
    TrackedAlarmRepository,
)
from app.domain.shared import UnitOfWork


def _build_alert_context(connection: CloudConnection, alarm: AlarmState) -> dict:
    description = f"CloudWatch alarm '{alarm.name}' is in ALARM state"
    if alarm.reason:
        description += f": {alarm.reason}"
    context: dict = {"service": connection.project, "alert": description}
    if alarm.metric_name:
        context["metrics"] = {"namespace": alarm.namespace, "metric_name": alarm.metric_name}
    return context


@dataclass
class PollAlarmsJob:
    connections: CloudConnectionRepository
    tracked: TrackedAlarmRepository
    fetcher: AlarmFetcher
    ingest: "IngestIncident"  # noqa: F821 - avoids a hard import cycle; see app/application/incidents/ingest.py
    resolve: "ResolveIncident"  # noqa: F821 - see app/application/incidents/resolve.py
    uow: UnitOfWork

    async def run(self) -> None:
        for connection in await self.connections.list():
            try:
                alarms = await self.fetcher.list_alarms(connection)
            except Exception as exc:  # noqa: BLE001 - isolate this connection's failure, keep polling others
                await self.connections.record_poll_result(
                    connection.id, status="error", error=str(exc)
                )
                await self.uow.commit()
                continue

            for alarm in alarms:
                await self._apply(connection, alarm)

            await self.connections.record_poll_result(connection.id, status="ok", error=None)
            await self.uow.commit()

    async def _apply(self, connection: CloudConnection, alarm: AlarmState) -> None:
        existing = await self.tracked.get(connection.id, alarm.arn)
        was_alarming = existing is not None and existing.last_state == "ALARM"

        if alarm.state == "ALARM" and not was_alarming:
            incident, _ = await self.ingest.execute(
                source="cloudwatch_alarm", context=_build_alert_context(connection, alarm)
            )
            await self.tracked.upsert(
                connection.id, alarm_arn=alarm.arn, alarm_name=alarm.name,
                last_state="ALARM", incident_id=incident.id,
            )
        elif alarm.state == "OK" and was_alarming:
            if existing.incident_id is not None:
                await self.resolve.resolve(
                    existing.incident_id,
                    resolution_notes=f"Auto-resolved: alarm '{alarm.name}' returned to OK",
                )
            await self.tracked.upsert(
                connection.id, alarm_arn=alarm.arn, alarm_name=alarm.name,
                last_state="OK", incident_id=None,
            )
```

Add the real imports for the type-hint comments (they're deferred as strings above only to keep this
task self-contained during review; wire the real imports now):

```python
from app.application.incidents.ingest import IngestIncident
from app.application.incidents.resolve import ResolveIncident
```

Place these two imports at the top of the file with the others, and change the two field
annotations from string-quoted (`"IngestIncident"`) to plain (`IngestIncident`), removing the
`# noqa` comments — the forward-reference workaround is unnecessary once the imports are present.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_poll_alarms_job.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/application/cloud_connections/ backend/tests/test_poll_alarms_job.py
git commit -m "feat: add PollAlarmsJob (CloudWatch alarm -> incident state machine)"
```

---

## Task 11: ManageCloudConnections (CRUD + test-connection)

**Files:**
- Create: `backend/app/application/cloud_connections/manage.py`
- Test: `backend/tests/test_manage_cloud_connections.py`

**Interfaces:**
- Consumes: `CloudConnectionRepository` (Task 5), `Encryptor` (Task 6), `AlarmFetcher` (Task 5), `UnitOfWork`.
- Produces: `ManageCloudConnections` with `create`, `list`, `delete`, `test` — consumed by Task 13 (HTTP router).

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for ManageCloudConnections — no DB, no network."""

import pytest

from app.application.cloud_connections.manage import ManageCloudConnections
from app.domain.cloud_connections.entities import CloudConnection
from app.infrastructure.security.encryptor import Encryptor

pytestmark = pytest.mark.asyncio

_KEY = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="


class FakeRepo:
    def __init__(self):
        self.rows = {}

    async def add(self, connection):
        connection.id = "generated-id"
        self.rows[connection.id] = connection
        return connection

    async def get(self, connection_id):
        return self.rows.get(connection_id)

    async def list(self):
        return list(self.rows.values())

    async def delete(self, connection_id):
        self.rows.pop(connection_id, None)


class FakeFetcher:
    def __init__(self, should_fail=False):
        self._should_fail = should_fail

    async def list_alarms(self, connection):
        if self._should_fail:
            raise RuntimeError("access denied")
        return []


class FakeUnitOfWork:
    async def commit(self):
        pass


def _manager(fetcher=None):
    return ManageCloudConnections(
        connections=FakeRepo(), encryptor=Encryptor(_KEY),
        fetcher=fetcher or FakeFetcher(), uow=FakeUnitOfWork(),
    )


async def test_create_encrypts_access_key_credentials():
    manager = _manager()
    connection = await manager.create(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="access_key",
        access_key_id="AKIAEXAMPLE", secret_access_key="supersecret",
    )
    assert connection.encrypted_access_key_id != "AKIAEXAMPLE"
    assert connection.encrypted_secret_access_key != "supersecret"


async def test_create_sso_connection_has_no_encrypted_fields():
    manager = _manager()
    connection = await manager.create(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="sso",
        sso_profile_name="GCM-Prod-ReadOnlyAccess",
    )
    assert connection.encrypted_access_key_id is None
    assert connection.encrypted_secret_access_key is None


async def test_test_connection_reports_success():
    manager = _manager(fetcher=FakeFetcher(should_fail=False))
    connection = await manager.create(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="sso", sso_profile_name="p"
    )
    ok, error = await manager.test(connection.id)
    assert ok is True
    assert error is None


async def test_test_connection_reports_failure():
    manager = _manager(fetcher=FakeFetcher(should_fail=True))
    connection = await manager.create(
        project="GCM", env="prod", region="ap-southeast-1", auth_type="sso", sso_profile_name="p"
    )
    ok, error = await manager.test(connection.id)
    assert ok is False
    assert error == "access denied"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_manage_cloud_connections.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.application.cloud_connections.manage'`

- [ ] **Step 3: Write the implementation**

```python
"""ManageCloudConnections: CRUD for cloud connections plus a test-connection action.

Encrypts access-key credentials on create; API DTOs (interface layer) never round-trip the
encrypted fields back out, so decryption only ever happens inside CredentialResolver at call time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.cloud_connections.entities import CloudConnection
from app.domain.cloud_connections.ports import AlarmFetcher, CloudConnectionRepository
from app.domain.shared import UnitOfWork
from app.infrastructure.security.encryptor import Encryptor


@dataclass
class ManageCloudConnections:
    connections: CloudConnectionRepository
    encryptor: Encryptor
    fetcher: AlarmFetcher
    uow: UnitOfWork

    async def create(
        self,
        *,
        project: str,
        env: str,
        region: str,
        auth_type: str,
        sso_profile_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> CloudConnection:
        connection = await self.connections.add(
            CloudConnection(
                project=project,
                env=env,
                region=region,
                auth_type=auth_type,
                sso_profile_name=sso_profile_name,
                encrypted_access_key_id=(
                    self.encryptor.encrypt(access_key_id) if access_key_id else None
                ),
                encrypted_secret_access_key=(
                    self.encryptor.encrypt(secret_access_key) if secret_access_key else None
                ),
            )
        )
        await self.uow.commit()
        return connection

    async def list(self) -> list[CloudConnection]:
        return await self.connections.list()

    async def delete(self, connection_id: uuid.UUID) -> None:
        await self.connections.delete(connection_id)
        await self.uow.commit()

    async def test(self, connection_id: uuid.UUID) -> tuple[bool, str | None]:
        connection = await self.connections.get(connection_id)
        if connection is None:
            raise ValueError(f"connection {connection_id} not found")
        try:
            await self.fetcher.list_alarms(connection)
            return True, None
        except Exception as exc:  # noqa: BLE001 - reported to the caller as a test result, not raised
            return False, str(exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_manage_cloud_connections.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/application/cloud_connections/manage.py backend/tests/test_manage_cloud_connections.py
git commit -m "feat: add ManageCloudConnections use case (CRUD + test-connection)"
```

---

## Task 12: HTTP DTOs

**Files:**
- Create: `backend/app/interface/http/dto/request/cloud_connection.py`
- Create: `backend/app/interface/http/dto/response/cloud_connection.py`
- Create: `backend/app/interface/http/dto/mappers/cloud_connection.py`
- Modify: `backend/app/interface/http/dto/request/__init__.py`
- Modify: `backend/app/interface/http/dto/response/__init__.py`
- Modify: `backend/app/interface/http/dto/mappers/__init__.py`

**Interfaces:**
- Consumes: `CloudConnection` (Task 4).
- Produces: `CloudConnectionCreateRequest`, `CloudConnectionOut`, `TestConnectionResult`, `mappers.cloud_connection_out` — consumed by Task 13 (HTTP router).

- [ ] **Step 1: Write the request DTO**

```python
"""Cloud-connection request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel


class CloudConnectionCreateRequest(BaseModel):
    """`POST /api/cloud-connections` body."""

    project: str
    env: str
    region: str
    auth_type: str  # sso | access_key (validated at the controller)
    sso_profile_name: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
```

- [ ] **Step 2: Write the response DTOs**

```python
"""Cloud-connection response DTOs. Deliberately omits the encrypted secret fields entirely — the
API never round-trips them, not even masked (design spec "Credentials & security")."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class CloudConnectionOut(BaseModel):
    """One row in `GET /api/cloud-connections`."""

    id: uuid.UUID
    project: str
    env: str
    cloud: str
    region: str
    auth_type: str
    sso_profile_name: str | None
    has_access_key: bool
    last_poll_at: datetime | None
    last_poll_status: str | None
    last_poll_error: str | None
    created_at: datetime


class TestConnectionResult(BaseModel):
    """`POST /api/cloud-connections/{id}/test` response."""

    ok: bool
    error: str | None


class PollResult(BaseModel):
    """`POST /api/cloud-connections/poll` (and `/{id}/poll`) response."""

    polled: int
```

- [ ] **Step 3: Write the mapper**

```python
"""Mappers: cloud-connection domain entities -> response DTOs."""

from __future__ import annotations

from app.domain.cloud_connections.entities import CloudConnection
from app.interface.http.dto.response.cloud_connection import CloudConnectionOut


def cloud_connection_out(connection: CloudConnection) -> CloudConnectionOut:
    return CloudConnectionOut(
        id=connection.id,
        project=connection.project,
        env=connection.env,
        cloud=connection.cloud,
        region=connection.region,
        auth_type=connection.auth_type,
        sso_profile_name=connection.sso_profile_name,
        has_access_key=connection.encrypted_access_key_id is not None,
        last_poll_at=connection.last_poll_at,
        last_poll_status=connection.last_poll_status,
        last_poll_error=connection.last_poll_error,
        created_at=connection.created_at,
    )
```

- [ ] **Step 4: Wire up the package `__init__.py` files**

In `backend/app/interface/http/dto/request/__init__.py`, add the import and `__all__` entry:

```python
from app.interface.http.dto.request.cloud_connection import CloudConnectionCreateRequest
```

In `backend/app/interface/http/dto/response/__init__.py`, add:

```python
from app.interface.http.dto.response.cloud_connection import (
    CloudConnectionOut,
    PollResult,
    TestConnectionResult,
)
```

In `backend/app/interface/http/dto/mappers/__init__.py`, add:

```python
from app.interface.http.dto.mappers.cloud_connection import cloud_connection_out
```

Add all new names to each file's `__all__` list.

- [ ] **Step 5: Verify it imports cleanly**

Run: `cd backend && uv run python -c "from app.interface.http.dto import request, response, mappers; print(request.CloudConnectionCreateRequest, response.CloudConnectionOut, mappers.cloud_connection_out)"`
Expected: prints the three class/function objects with no error.

- [ ] **Step 6: Commit**

```bash
git add backend/app/interface/http/dto/
git commit -m "feat: add cloud-connection HTTP DTOs"
```

---

## Task 13: HTTP router

**Files:**
- Create: `backend/app/interface/http/cloud_connections.py`

**Interfaces:**
- Consumes: `ManageCloudConnections` (Task 11), `PollAlarmsJob` (Task 10), DTOs (Task 12) — dependency providers from Task 14.
- Produces: `router` (FastAPI `APIRouter`) — consumed by Task 15 (`main.py`).

- [ ] **Step 1: Write the router**

```python
"""Cloud-connection HTTP controller: CRUD for per-account AWS connections, a test-connection
action, and manual poll triggers (the Settings page "Refresh" button)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.cloud_connections.manage import ManageCloudConnections
from app.application.cloud_connections.poll_alarms import PollAlarmsJob
from app.interface.http.deps import get_manage_cloud_connections, get_poll_alarms_job
from app.interface.http.dto import mappers
from app.interface.http.dto.request import CloudConnectionCreateRequest
from app.interface.http.dto.response import CloudConnectionOut, PollResult, TestConnectionResult

router = APIRouter(prefix="/api/cloud-connections", tags=["cloud-connections"])

_AUTH_TYPES = {"sso", "access_key"}


@router.post("", response_model=CloudConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: CloudConnectionCreateRequest,
    manager: ManageCloudConnections = Depends(get_manage_cloud_connections),
) -> CloudConnectionOut:
    if body.auth_type not in _AUTH_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"auth_type must be one of {sorted(_AUTH_TYPES)}",
        )
    if body.auth_type == "sso" and not body.sso_profile_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sso_profile_name is required when auth_type is 'sso'",
        )
    if body.auth_type == "access_key" and not (body.access_key_id and body.secret_access_key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="access_key_id and secret_access_key are required when auth_type is 'access_key'",
        )
    connection = await manager.create(
        project=body.project, env=body.env, region=body.region, auth_type=body.auth_type,
        sso_profile_name=body.sso_profile_name, access_key_id=body.access_key_id,
        secret_access_key=body.secret_access_key,
    )
    return mappers.cloud_connection_out(connection)


@router.get("", response_model=list[CloudConnectionOut])
async def list_connections(
    manager: ManageCloudConnections = Depends(get_manage_cloud_connections),
) -> list[CloudConnectionOut]:
    connections = await manager.list()
    return [mappers.cloud_connection_out(c) for c in connections]


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    manager: ManageCloudConnections = Depends(get_manage_cloud_connections),
) -> None:
    await manager.delete(connection_id)


@router.post("/{connection_id}/test", response_model=TestConnectionResult)
async def test_connection(
    connection_id: uuid.UUID,
    manager: ManageCloudConnections = Depends(get_manage_cloud_connections),
) -> TestConnectionResult:
    try:
        ok, error = await manager.test(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TestConnectionResult(ok=ok, error=error)


@router.post("/poll", response_model=PollResult)
async def poll_all(job: PollAlarmsJob = Depends(get_poll_alarms_job)) -> PollResult:
    connections = await job.connections.list()
    await job.run()
    return PollResult(polled=len(connections))
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd backend && uv run python -c "from app.interface.http.cloud_connections import router; print(router.prefix)"`
Expected: `/api/cloud-connections` (will fail until Task 14 adds `get_manage_cloud_connections`/`get_poll_alarms_job` — that's expected; re-run after Task 14).

- [ ] **Step 3: Commit**

```bash
git add backend/app/interface/http/cloud_connections.py
git commit -m "feat: add cloud-connections HTTP router"
```

---

## Task 14: Dependency wiring

**Files:**
- Modify: `backend/app/interface/http/deps.py`

**Interfaces:**
- Consumes: everything from Tasks 5-11.
- Produces: `get_cloud_connection_repository`, `get_manage_cloud_connections`, `get_poll_alarms_job` — consumed by Task 13 (router) and Task 15 (scheduler).

- [ ] **Step 1: Add the dependency providers**

In `backend/app/interface/http/deps.py`, add these imports alongside the existing ones:

```python
from app.application.cloud_connections.manage import ManageCloudConnections
from app.application.cloud_connections.poll_alarms import PollAlarmsJob
from app.domain.cloud_connections.ports import AlarmFetcher, CloudConnectionRepository
from app.infrastructure.cloud.cloudwatch_alarms import CloudWatchAlarmFetcher
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.db.repositories import (
    SqlAlchemyCloudConnectionRepository,
    SqlAlchemyTrackedAlarmRepository,
)
from app.infrastructure.security.encryptor import Encryptor
```

(add `SqlAlchemyCloudConnectionRepository`, `SqlAlchemyTrackedAlarmRepository` into the existing
`from app.infrastructure.db.repositories import (...)` block instead of a second import line).

Then append these functions at the end of the file:

```python
def get_encryptor() -> Encryptor:
    return Encryptor(get_settings().secret_encryption_key)


def get_cloud_connection_repository(
    session: AsyncSession = Depends(get_session),
) -> CloudConnectionRepository:
    return SqlAlchemyCloudConnectionRepository(session)


def get_alarm_fetcher(
    encryptor: Encryptor = Depends(get_encryptor),
) -> AlarmFetcher:
    """Tests override this to avoid a real AWS call (same convention as get_ticket_client)."""
    return CloudWatchAlarmFetcher(CredentialResolver(encryptor))


def get_manage_cloud_connections(
    connections: CloudConnectionRepository = Depends(get_cloud_connection_repository),
    encryptor: Encryptor = Depends(get_encryptor),
    fetcher: AlarmFetcher = Depends(get_alarm_fetcher),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
) -> ManageCloudConnections:
    return ManageCloudConnections(
        connections=connections, encryptor=encryptor, fetcher=fetcher, uow=uow
    )


def get_poll_alarms_job(
    session: AsyncSession = Depends(get_session),
    fetcher: AlarmFetcher = Depends(get_alarm_fetcher),
) -> PollAlarmsJob:
    """Manual-trigger path (`POST /api/cloud-connections/poll`). The scheduled path builds its own
    job with an independent session — see `build_scheduled_poll_job` in main.py."""
    settings = get_settings()
    return PollAlarmsJob(
        connections=SqlAlchemyCloudConnectionRepository(session),
        tracked=SqlAlchemyTrackedAlarmRepository(session),
        fetcher=fetcher,
        ingest=IngestIncident(
            incidents=SqlAlchemyIncidentRepository(session),
            cache=SqlAlchemyAnalysisCacheRepository(session),
            analyzer=get_analyzer(session=session, base=get_base_analyzer(), embedder=get_embedder()),
            clock=SystemClock(),
            uow=SqlAlchemyUnitOfWork(session),
            cache_ttl_seconds=settings.cache_ttl_seconds,
        ),
        resolve=ResolveIncident(
            incidents=SqlAlchemyIncidentRepository(session),
            documents=SqlAlchemyDocumentRepository(session),
            embedder=get_embedder(),
            uow=SqlAlchemyUnitOfWork(session),
        ),
        uow=SqlAlchemyUnitOfWork(session),
    )
```

- [ ] **Step 2: Verify the router now imports cleanly**

Run: `cd backend && uv run python -c "from app.interface.http.cloud_connections import router; print(router.prefix)"`
Expected: `/api/cloud-connections`

- [ ] **Step 3: Commit**

```bash
git add backend/app/interface/http/deps.py
git commit -m "feat: wire cloud-connection dependencies"
```

---

## Task 15: App wiring — router + scheduled polling

**Files:**
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `router` (Task 13), `PollAlarmsJob` build logic (Task 14's pattern, rebuilt here with its own session for the background scheduler).
- Produces: the app now polls every `settings.alarm_poll_interval_minutes` minutes automatically.

- [ ] **Step 1: Add the scheduled-job builder and lifespan**

Replace the top of `backend/app/main.py` (imports and app construction) with:

```python
"""IIM FastAPI application entrypoint.

Composes the interface-layer HTTP routers over the application/domain core (clean-architecture
layering, docs/ARCHITECTURE.md). A background APScheduler job polls CloudWatch alarms hourly
(design spec 2026-08-22-cloudwatch-alarm-polling-design.md) using its own DB session, independent
of any request.

FastAPI docs: https://fastapi.tiangolo.com/
APScheduler docs: https://apscheduler.readthedocs.io/
"""

from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.application.cloud_connections.poll_alarms import PollAlarmsJob
from app.application.incidents.ingest import IngestIncident
from app.application.incidents.resolve import ResolveIncident
from app.infrastructure.clock import SystemClock
from app.infrastructure.config import get_settings
from app.infrastructure.db.repositories import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyCloudConnectionRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyIncidentRepository,
    SqlAlchemyTrackedAlarmRepository,
    SqlAlchemyUnitOfWork,
)
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.cloud.cloudwatch_alarms import CloudWatchAlarmFetcher
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.security.encryptor import Encryptor
from app.interface.http.cloud_connections import router as cloud_connections_router
from app.interface.http.deps import get_analyzer, get_base_analyzer, get_embedder
from app.interface.http.documents import router as documents_router
from app.interface.http.health import router as health_router
from app.interface.http.incidents import router as incidents_router
from app.interface.http.reports import router as reports_router

settings = get_settings()


async def _run_scheduled_poll() -> None:
    """Builds a PollAlarmsJob with its own session and runs one poll cycle. Wired into
    AsyncIOScheduler below; the manual `/api/cloud-connections/poll` endpoint uses the same
    PollAlarmsJob class via a request-scoped session in deps.get_poll_alarms_job."""
    async with SessionLocal() as session:
        embedder = get_embedder()
        job = PollAlarmsJob(
            connections=SqlAlchemyCloudConnectionRepository(session),
            tracked=SqlAlchemyTrackedAlarmRepository(session),
            fetcher=CloudWatchAlarmFetcher(
                CredentialResolver(Encryptor(settings.secret_encryption_key))
            ),
            ingest=IngestIncident(
                incidents=SqlAlchemyIncidentRepository(session),
                cache=SqlAlchemyAnalysisCacheRepository(session),
                analyzer=get_analyzer(session=session, base=get_base_analyzer(), embedder=embedder),
                clock=SystemClock(),
                uow=SqlAlchemyUnitOfWork(session),
                cache_ttl_seconds=settings.cache_ttl_seconds,
            ),
            resolve=ResolveIncident(
                incidents=SqlAlchemyIncidentRepository(session),
                documents=SqlAlchemyDocumentRepository(session),
                embedder=embedder,
                uow=SqlAlchemyUnitOfWork(session),
            ),
            uow=SqlAlchemyUnitOfWork(session),
        )
        await job.run()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_scheduled_poll, "interval", minutes=settings.alarm_poll_interval_minutes,
        id="poll_cloudwatch_alarms",
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title=f"{settings.app_name} API",
    description="Intelligent Incident Management - AI incident triage grounded in RAG.",
    version="0.1.0",
    lifespan=lifespan,
)
```

- [ ] **Step 2: Register the router**

Keep the existing `app.add_middleware(...)` block and `app.include_router(...)` calls, adding one line:

```python
app.include_router(cloud_connections_router)
```

- [ ] **Step 3: Verify the app still boots**

Run: `cd backend && uv run python -c "from app.main import app; print([r.path for r in app.routes if 'cloud-connections' in r.path])"`
Expected: `['/api/cloud-connections', '/api/cloud-connections', '/api/cloud-connections/{connection_id}', '/api/cloud-connections/{connection_id}/test', '/api/cloud-connections/poll']`

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: schedule hourly CloudWatch alarm polling and mount its router"
```

---

## Task 16: HTTP integration tests

**Files:**
- Create: `backend/tests/test_cloud_connections_http.py`

**Interfaces:**
- Consumes: the full stack from Tasks 1-15, following the `test_documents_http.py` pattern (real Postgres, fakes for external calls).

- [ ] **Step 1: Write the tests**

```python
"""End-to-end HTTP tests for /api/cloud-connections against real Postgres.

Overrides get_alarm_fetcher with a fake so no real AWS call happens (same pattern as
test_documents_http.py overriding get_embedder).
"""

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cloud_connections.entities import AlarmState
from app.infrastructure.db.orm import Base, CloudConnectionRow, TrackedAlarmRow
from app.interface.http.deps import get_alarm_fetcher, get_session
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)


class _FakeFetcher:
    def __init__(self, alarms=None, should_fail=False):
        self._alarms = alarms or []
        self._should_fail = should_fail

    async def list_alarms(self, connection):
        if self._should_fail:
            raise RuntimeError("access denied")
        return self._alarms


@pytest.fixture()
async def client():
    engine = create_async_engine(_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"Postgres not reachable for HTTP test: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(TrackedAlarmRow))
        await s.execute(delete(CloudConnectionRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_alarm_fetcher] = lambda: _FakeFetcher()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_create_list_and_delete_sso_connection(client):
    r = await client.post(
        "/api/cloud-connections",
        json={
            "project": "GCM", "env": "prod", "region": "ap-southeast-1",
            "auth_type": "sso", "sso_profile_name": "GCM-Prod-ReadOnlyAccess",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["sso_profile_name"] == "GCM-Prod-ReadOnlyAccess"
    assert created["has_access_key"] is False

    r = await client.get("/api/cloud-connections")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = await client.delete(f"/api/cloud-connections/{created['id']}")
    assert r.status_code == 204
    r = await client.get("/api/cloud-connections")
    assert r.json() == []


async def test_create_access_key_connection_never_returns_the_secret(client):
    r = await client.post(
        "/api/cloud-connections",
        json={
            "project": "GCM", "env": "dev", "region": "us-east-1", "auth_type": "access_key",
            "access_key_id": "AKIAEXAMPLE", "secret_access_key": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_access_key"] is True
    assert "access_key_id" not in body
    assert "secret_access_key" not in body
    assert "encrypted_access_key_id" not in body


async def test_missing_sso_profile_name_is_422(client):
    r = await client.post(
        "/api/cloud-connections",
        json={"project": "GCM", "env": "prod", "region": "us-east-1", "auth_type": "sso"},
    )
    assert r.status_code == 422


async def test_test_connection_endpoint_reports_ok(client):
    r = await client.post(
        "/api/cloud-connections",
        json={"project": "GCM", "env": "prod", "region": "us-east-1", "auth_type": "sso", "sso_profile_name": "p"},
    )
    connection_id = r.json()["id"]
    r = await client.post(f"/api/cloud-connections/{connection_id}/test")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "error": None}


async def test_poll_endpoint_creates_incident_from_alarming_connection(client):
    app.dependency_overrides[get_alarm_fetcher] = lambda: _FakeFetcher(
        alarms=[AlarmState(arn="arn:1", name="cpu-high", state="ALARM", reason="high cpu")]
    )
    r = await client.post(
        "/api/cloud-connections",
        json={"project": "GCM", "env": "prod", "region": "us-east-1", "auth_type": "sso", "sso_profile_name": "p"},
    )
    assert r.status_code == 201

    r = await client.post("/api/cloud-connections/poll")
    assert r.status_code == 200, r.text
    assert r.json() == {"polled": 1}

    r = await client.get("/api/incidents")
    assert r.status_code == 200
    sources = [i["source"] for i in r.json()]
    assert "cloudwatch_alarm" in sources
```

Note: the last test's incident creation runs through the real `IngestIncident`/analyzer path built
in `get_poll_alarms_job` — if `ANALYSIS_MODE=single` and `LLM_PROVIDER` needs a live key in your
environment, this test will attempt a real LLM call and may fail/skip in CI without credentials.
That mirrors the existing `test_documents_http.py`'s embedder override; if this becomes a problem in
practice, override `get_analyzer`/`get_base_analyzer` too (same dependency-override mechanism), but
try it as written first since `get_poll_alarms_job` already reuses whatever `get_base_analyzer`
override is active.

- [ ] **Step 2: Run the tests**

Run: `cd backend && EMBEDDING_DIM=768 uv run pytest tests/test_cloud_connections_http.py -v`
Expected: `5 passed` (or explicit skips if Postgres isn't reachable — see `start-demo` skill's
"Running pytest while the stack is up" note for the `EMBEDDING_DIM=768` requirement).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_cloud_connections_http.py
git commit -m "test: add HTTP integration tests for cloud connections and manual poll"
```

---

## Task 17: Frontend — API client, types, nav

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/nav.ts`

**Interfaces:**
- Produces: `api.del`, `api.post` (existing, reused for `/test` and `/poll`), `CloudConnection` type, `'settings'` added to `View` — consumed by Task 18.

- [ ] **Step 1: Add `del` to the API client**

In `frontend/src/lib/api.ts`, add to the `api` object (alongside `get`/`post`):

```typescript
  del: <T>(path: string): Promise<T> => fetch(path, { method: 'DELETE' }).then((r) => handle<T>(r)),
```

Note: `handle<T>` already tolerates an empty body (`text ? JSON.parse(text) : null`), so a 204
response from `DELETE` resolves to `null` without changes.

- [ ] **Step 2: Add the CloudConnection type**

In `frontend/src/lib/types.ts`, add:

```typescript
export interface CloudConnection {
  id: string
  project: string
  env: string
  cloud: string
  region: string
  auth_type: 'sso' | 'access_key'
  sso_profile_name: string | null
  has_access_key: boolean
  last_poll_at: string | null
  last_poll_status: 'ok' | 'error' | null
  last_poll_error: string | null
  created_at: string
}

export interface CloudConnectionCreate {
  project: string
  env: string
  region: string
  auth_type: 'sso' | 'access_key'
  sso_profile_name?: string
  access_key_id?: string
  secret_access_key?: string
}

export interface TestConnectionResult {
  ok: boolean
  error: string | null
}

export interface PollResult {
  polled: number
}
```

- [ ] **Step 3: Promote Settings from "soon" to a real nav item**

In `frontend/src/lib/nav.ts`, change `export type View = 'overview' | 'incidents' | 'knowledge' | 'reports'` to:

```typescript
export type View = 'overview' | 'incidents' | 'knowledge' | 'reports' | 'settings'
```

Move the `Settings` entry out of the `System` section's `soon` array into a real `items` entry:

```typescript
  {
    heading: 'System',
    items: [{ view: 'settings', label: 'Settings', icon: Settings }],
    soon: [
      { label: 'Services', icon: Server },
      { label: 'Team', icon: Users },
    ],
  },
```

Add a `VIEW_META['settings']` entry:

```typescript
  settings: {
    title: 'Settings',
    subtitle: 'AWS connections used to poll CloudWatch alarms into incidents',
  },
```

- [ ] **Step 4: Verify the frontend still typechecks**

Run: `cd frontend && npm run build`
Expected: builds with no TypeScript errors (Task 18 still needs to add the `Settings` page component before `App.tsx` references it, so if `App.tsx` isn't touched yet this passes on its own).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/lib/nav.ts
git commit -m "feat: add cloud-connection types, DELETE client, and Settings nav entry"
```

---

## Task 18: Frontend — Settings page

**Files:**
- Create: `frontend/src/features/settings/CloudConnectionForm.tsx`
- Create: `frontend/src/features/settings/CloudConnectionTable.tsx`
- Create: `frontend/src/pages/Settings.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `CloudConnection`, `CloudConnectionCreate`, `TestConnectionResult`, `PollResult` (Task 17), `api.get/post/del` (Task 17), `Button`/`Card`/`Badge` UI primitives (existing, `frontend/src/components/ui/`).

- [ ] **Step 1: Write the connection form**

```tsx
import { useState } from 'react'
import { api, errText } from '../../lib/api'
import type { CloudConnection, CloudConnectionCreate } from '../../lib/types'
import { Button } from '../../components/ui/Button'

const KNOWN_PROJECTS = ['BEC', 'EVP', 'GCM', 'SmartSuite', 'IIM']

export function CloudConnectionForm({ onCreated }: { onCreated: (c: CloudConnection) => void }) {
  const [project, setProject] = useState(KNOWN_PROJECTS[0])
  const [env, setEnv] = useState('prod')
  const [region, setRegion] = useState('ap-southeast-1')
  const [authType, setAuthType] = useState<'sso' | 'access_key'>('sso')
  const [ssoProfileName, setSsoProfileName] = useState('')
  const [accessKeyId, setAccessKeyId] = useState('')
  const [secretAccessKey, setSecretAccessKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    const body: CloudConnectionCreate = {
      project,
      env,
      region,
      auth_type: authType,
      ...(authType === 'sso'
        ? { sso_profile_name: ssoProfileName }
        : { access_key_id: accessKeyId, secret_access_key: secretAccessKey }),
    }
    try {
      const created = await api.post<CloudConnection>('/api/cloud-connections', body)
      onCreated(created)
      setSsoProfileName('')
      setAccessKeyId('')
      setSecretAccessKey('')
    } catch (e) {
      setError(errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-2xl border border-[var(--border)] p-4">
      <div className="flex gap-3">
        <label className="flex-1 text-sm">
          Project
          <select
            value={project}
            onChange={(e) => setProject(e.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--border)] bg-surface p-2"
          >
            {KNOWN_PROJECTS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="flex-1 text-sm">
          Env
          <input
            value={env}
            onChange={(e) => setEnv(e.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--border)] bg-surface p-2"
          />
        </label>
        <label className="flex-1 text-sm">
          Region
          <input
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--border)] bg-surface p-2"
          />
        </label>
      </div>

      <div className="flex gap-4 text-sm">
        <label className="flex items-center gap-2">
          <input
            type="radio"
            checked={authType === 'sso'}
            onChange={() => setAuthType('sso')}
          />
          SSO profile
        </label>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            checked={authType === 'access_key'}
            onChange={() => setAuthType('access_key')}
          />
          Access key
        </label>
      </div>

      {authType === 'sso' ? (
        <label className="text-sm">
          SSO profile name (from the host's ~/.aws/config)
          <input
            value={ssoProfileName}
            onChange={(e) => setSsoProfileName(e.target.value)}
            placeholder="GCM-Prod-ReadOnlyAccess"
            className="mt-1 w-full rounded-lg border border-[var(--border)] bg-surface p-2"
            required
          />
        </label>
      ) : (
        <div className="flex gap-3">
          <label className="flex-1 text-sm">
            Access key ID
            <input
              value={accessKeyId}
              onChange={(e) => setAccessKeyId(e.target.value)}
              className="mt-1 w-full rounded-lg border border-[var(--border)] bg-surface p-2"
              required
            />
          </label>
          <label className="flex-1 text-sm">
            Secret access key
            <input
              type="password"
              value={secretAccessKey}
              onChange={(e) => setSecretAccessKey(e.target.value)}
              className="mt-1 w-full rounded-lg border border-[var(--border)] bg-surface p-2"
              required
            />
          </label>
        </div>
      )}

      {error && <p className="text-sm text-[var(--sev-critical)]">{error}</p>}
      <Button type="submit" disabled={submitting}>
        {submitting ? 'Adding…' : 'Add connection'}
      </Button>
    </form>
  )
}
```

- [ ] **Step 2: Write the connections table**

```tsx
import { useState } from 'react'
import { api, errText } from '../../lib/api'
import type { CloudConnection, TestConnectionResult } from '../../lib/types'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'

export function CloudConnectionTable({
  rows,
  onDeleted,
}: {
  rows: CloudConnection[]
  onDeleted: (id: string) => void
}) {
  const [testResults, setTestResults] = useState<Record<string, TestConnectionResult>>({})
  const [busy, setBusy] = useState<string | null>(null)

  const test = async (id: string) => {
    setBusy(id)
    try {
      const result = await api.post<TestConnectionResult>(`/api/cloud-connections/${id}/test`, {})
      setTestResults((prev) => ({ ...prev, [id]: result }))
    } catch (e) {
      setTestResults((prev) => ({ ...prev, [id]: { ok: false, error: errText(e) } }))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (id: string) => {
    setBusy(id)
    try {
      await api.del(`/api/cloud-connections/${id}`)
      onDeleted(id)
    } finally {
      setBusy(null)
    }
  }

  if (rows.length === 0) {
    return <p className="text-sm text-[var(--muted)]">No cloud connections yet.</p>
  }

  return (
    <table className="w-full text-sm">
      <thead className="text-left text-[var(--muted)]">
        <tr>
          <th className="py-2">Project / Env</th>
          <th>Auth</th>
          <th>Region</th>
          <th>Last poll</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => {
          const result = testResults[c.id]
          return (
            <tr key={c.id} className="border-t border-[var(--border)]">
              <td className="py-2">
                {c.project} / {c.env}
              </td>
              <td>{c.auth_type === 'sso' ? c.sso_profile_name : 'access key'}</td>
              <td>{c.region}</td>
              <td>
                {c.last_poll_status ? (
                  <Badge tone={c.last_poll_status === 'ok' ? 'success' : 'critical'}>
                    {c.last_poll_status}
                  </Badge>
                ) : (
                  <Badge tone="info">never polled</Badge>
                )}
              </td>
              <td className="flex gap-2 py-2">
                <Button variant="ghost" disabled={busy === c.id} onClick={() => test(c.id)}>
                  Test
                </Button>
                <Button variant="ghost" disabled={busy === c.id} onClick={() => remove(c.id)}>
                  Delete
                </Button>
                {result && (
                  <span className={result.ok ? 'text-[var(--sev-low)]' : 'text-[var(--sev-critical)]'}>
                    {result.ok ? 'OK' : result.error}
                  </span>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
```

Note: `Badge`'s `tone` prop values (`'success' | 'critical' | 'info'`, or whatever the component
actually defines) and `Button`'s `variant` prop must match `frontend/src/components/ui/Badge.tsx`
and `Button.tsx` exactly — read both files before implementing this step and adjust the prop values
used above to whatever those components actually accept.

- [ ] **Step 3: Write the Settings page**

```tsx
import { useEffect, useState } from 'react'
import { api, errText } from '../lib/api'
import type { CloudConnection, PollResult } from '../lib/types'
import { CloudConnectionForm } from '../features/settings/CloudConnectionForm'
import { CloudConnectionTable } from '../features/settings/CloudConnectionTable'
import { Button } from '../components/ui/Button'

export function Settings() {
  const [connections, setConnections] = useState<CloudConnection[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      setConnections(await api.get<CloudConnection[]>('/api/cloud-connections'))
    } catch (e) {
      setError(errText(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const refreshNow = async () => {
    setRefreshing(true)
    try {
      await api.post<PollResult>('/api/cloud-connections/poll', {})
      await load()
    } catch (e) {
      setError(errText(e))
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
      <div className="animate-in flex flex-col gap-6">
        <CloudConnectionForm onCreated={(c) => setConnections((prev) => [...prev, c])} />

        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--muted)]">AWS connections</h3>
          <Button variant="ghost" disabled={refreshing} onClick={refreshNow}>
            {refreshing ? 'Refreshing…' : 'Refresh now'}
          </Button>
        </div>

        {loading && <p className="text-sm text-[var(--muted)]">Loading…</p>}
        {error && <p className="text-sm text-[var(--sev-critical)]">{error}</p>}
        {!loading && !error && (
          <CloudConnectionTable
            rows={connections}
            onDeleted={(id) => setConnections((prev) => prev.filter((c) => c.id !== id))}
          />
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Wire the Settings view into App.tsx**

In `frontend/src/App.tsx`, add the import:

```typescript
import { Settings } from './pages/Settings'
```

Add the render branch alongside the existing `view === '...'` blocks:

```tsx
            {view === 'settings' && <Settings />}
```

Also adjust the `action` conditional (the `+New` button in the header) so Settings shows no
primary action, matching how `reports` is already excluded:

```typescript
  const action =
    view === 'knowledge' ? (
      <Button onClick={() => setShowDoc(true)}>
        <Plus size={16} /> New document
      </Button>
    ) : view === 'reports' || view === 'settings' ? undefined : (
      <Button onClick={() => setShowIncident(true)}>
        <Plus size={16} /> New incident
      </Button>
    )
```

- [ ] **Step 5: Manually verify in the browser**

Run: `cd frontend && npm run dev` (with `docker compose up db backend` running per `start-demo`)
Then open http://localhost:5173, navigate to Settings, add an SSO connection, click Test (expect a
result reflecting whether that SSO profile is actually logged in), click Refresh now, and confirm no
console errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/settings/ frontend/src/pages/Settings.tsx frontend/src/App.tsx
git commit -m "feat: add Settings page for managing cloud connections"
```

---

## Self-Review Notes

- **Spec coverage**: data model (Tasks 2-4), credentials/security (Tasks 6, 8, 11, 12 — DTO omits
  secrets entirely, stronger than the spec's "mask as ****"), polling/state machine (Task 10),
  API surface (Tasks 12-13), frontend (Tasks 17-18), scheduler at 60 min (Task 15), manual refresh
  (Task 13's `/poll`, Task 18's button) — all covered.
- **Incident "source" badge**: the spec called for a UI badge distinguishing alarm-sourced
  incidents; `IncidentDetail.tsx:126` already renders `<Badge>{d.source}</Badge>` with the raw
  `source` string, and `source` is unconstrained free text end-to-end (`Incident.source` docstring:
  `auto | manual | webhook` — not enforced), so `source="cloudwatch_alarm"` displays automatically.
  No frontend change needed for this — removed as a redundant task.
- **Known limitations carried into code as comments, not fixed**: flapping alarms (Task 10's
  `_apply` has no debounce — matches spec's accepted risk), no tag/prefix filtering (Task 9 lists
  every alarm), no KMS (Task 6 is app-level Fernet only) — all intentional, do not "improve" these
  during implementation.
