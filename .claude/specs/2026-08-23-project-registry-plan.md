# Project Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two independently-hardcoded, free-text `KNOWN_PROJECTS` lists (Settings'
CloudConnection and AdoConnection forms) with one shared `projects` registry table that both
connection types are database-enforced to reference.

**Architecture:** A new `projects` table (`id`, `name` UNIQUE, `created_at`) following the existing
hexagonal layering (domain entity/port → SQLAlchemy repository → application use case → HTTP
controller), mirroring `ado_connections` file-for-file. `cloud_connections.project` and
`ado_connections.project` stay TEXT columns but gain a database-level foreign key to
`projects.name` (not an ID column — see the spec's "Why a text FK, not an ID FK").

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async ORM, Alembic (hand-authored migrations), Postgres 16,
asyncpg driver, pytest + pytest-asyncio, React + TypeScript + Tailwind (no component library).

**Spec:** `.claude/specs/2026-08-23-project-registry-design.md`

## Global Constraints

- `projects.name` is `TEXT NOT NULL UNIQUE`. `cloud_connections.project` and
  `ado_connections.project` stay `TEXT` and get
  `FOREIGN KEY (project) REFERENCES projects(name) ON UPDATE CASCADE ON DELETE RESTRICT` — no
  `project_id` UUID column on either table (spec: "Why a text FK, not an ID FK").
- `incidents.service` gets **no** FK/constraint change — ingest must stay permissive for any
  service name (spec Non-goals).
- No rename endpoint or UI in this iteration — `ProjectRepository`/`ManageProjects` expose only
  `add`/`list`/`get`/`delete` (spec Non-goals).
- The migration's backfill step (distinct existing `project` values → `projects` rows) must run
  in the same `upgrade()`, before the FK constraints are added, so existing rows never violate the
  new constraint.
- Every new `/api/projects` endpoint is admin-gated (`dependencies=[Depends(require_admin)]` at the
  router level), matching `cloud_connections.py`/`ado_connections.py`.
- `ManageCloudConnections.create`/`.update` and `ManageAdoConnections.create`/`.update` must catch
  the new FK-violation `IntegrityError` and raise `UnknownProjectError`, which their HTTP routes
  map to `422` — this applies to both `create` and `update`, since editing a connection's `project`
  field is reachable through the same forms (spec: "Existing connection-creation paths...").
- `CloudConnectionForm.tsx`'s `SelectOrOtherField` usage for **Env** and **Region** is unrelated to
  this change and must not be touched — only its **Project** field changes.

---

### Task 1: Project domain layer, persistence, and repository

**Files:**
- Create: `backend/app/domain/projects/__init__.py` (empty)
- Create: `backend/app/domain/projects/entities.py`
- Create: `backend/app/domain/projects/ports.py`
- Create: `backend/app/domain/projects/errors.py`
- Create: `backend/migrations/versions/0013_projects.py`
- Modify: `backend/app/infrastructure/db/orm.py` (add `ProjectRow`; add the FK to
  `CloudConnectionRow.project`/`AdoConnectionRow.project`)
- Modify: `backend/app/infrastructure/db/repositories/mappers.py` (add `project_to_domain`)
- Create: `backend/app/infrastructure/db/repositories/projects.py`
- Modify: `backend/app/infrastructure/db/repositories/__init__.py` (export
  `SqlAlchemyProjectRepository`)
- Test: `backend/tests/test_project_repository.py`

**Interfaces:**
- Produces: `Project(name: str, id: uuid.UUID | None = None, created_at: datetime | None = None)`
  (`app.domain.projects.entities.Project`); `ProjectRepository` Protocol with
  `add(project) -> Project`, `get(project_id) -> Project | None`, `list() -> list[Project]`,
  `delete(project_id) -> None` (`app.domain.projects.ports.ProjectRepository`);
  `ProjectNameTakenError(name)`, `ProjectInUseError(project_id)`, `UnknownProjectError(name)`
  (`app.domain.projects.errors`) — plain `Exception` subclasses, no SQLAlchemy/asyncpg imports.
  `SqlAlchemyProjectRepository(session)` implementing the port
  (`app.infrastructure.db.repositories.projects`), re-exported from
  `app.infrastructure.db.repositories`.

- [ ] **Step 1: Write the failing repository test**

```python
# backend/tests/test_project_repository.py
"""Integration tests for SqlAlchemyProjectRepository against real Postgres.

Skipped when no database is reachable (same convention as test_app_settings_repository.py).
"""

import os
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.projects.entities import Project
from app.infrastructure.db.orm import AdoConnectionRow, Base, CloudConnectionRow, ProjectRow
from app.infrastructure.db.repositories.projects import SqlAlchemyProjectRepository

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
        # `projects` is now shared across every test file that touches cloud_connections/
        # ado_connections (they all reference it by name via the new FK) — delete the two
        # referencing tables before `projects` itself, and delete `projects` unconditionally
        # here, so a leftover row from a *different* test file's run never collides with (or
        # blocks the delete of) the names this file uses.
        await s.execute(delete(CloudConnectionRow))
        await s.execute(delete(AdoConnectionRow))
        await s.execute(delete(ProjectRow))
        await s.commit()
        yield s

    await engine.dispose()


async def test_add_then_list_round_trips(session):
    repo = SqlAlchemyProjectRepository(session)
    created = await repo.add(Project(name="EVP"))
    await session.commit()
    assert created.id is not None
    names = [p.name for p in await repo.list()]
    assert names == ["EVP"]


async def test_get_returns_none_for_unknown_id(session):
    repo = SqlAlchemyProjectRepository(session)
    assert await repo.get(uuid.uuid4()) is None


async def test_add_duplicate_name_raises_integrity_error(session):
    repo = SqlAlchemyProjectRepository(session)
    await repo.add(Project(name="EVP"))
    await session.commit()
    with pytest.raises(IntegrityError):
        await repo.add(Project(name="EVP"))
    await session.rollback()


async def test_delete_removes_the_row(session):
    repo = SqlAlchemyProjectRepository(session)
    created = await repo.add(Project(name="EVP"))
    await session.commit()
    await repo.delete(created.id)
    await session.commit()
    assert await repo.list() == []


async def test_delete_raises_integrity_error_when_referenced(session):
    repo = SqlAlchemyProjectRepository(session)
    created = await repo.add(Project(name="EVP"))
    await session.flush()
    session.add(
        CloudConnectionRow(
            id=uuid.uuid4(), project="EVP", env="prod", region="us-east-1", auth_type="sso",
            sso_profile_name="p",
        )
    )
    await session.commit()
    with pytest.raises(IntegrityError):
        await repo.delete(created.id)
    await session.rollback()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_project_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.projects'` (nothing exists yet).

- [ ] **Step 3: Write the domain layer**

```python
# backend/app/domain/projects/entities.py
"""Domain entity for the shared project registry — the canonical list of project names that
CloudConnection/AdoConnection (and any future per-project connection type) reference. Plain
dataclass, no ORM/framework coupling — same convention as domain/ado_connections/entities.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Project:
    """One registered internal project name (e.g. 'EVP', 'rxdevs')."""

    name: str
    id: uuid.UUID | None = None
    created_at: datetime | None = None
```

```python
# backend/app/domain/projects/ports.py
"""Ports the project-registry use cases depend on. Implemented in the infrastructure layer.

Same dependency-inversion convention as domain/ado_connections/ports.py.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.domain.projects.entities import Project

__all__ = ["ProjectRepository"]


class ProjectRepository(Protocol):
    """Persistence for the shared project registry."""

    async def add(self, project: Project) -> Project: ...

    async def get(self, project_id: uuid.UUID) -> Project | None: ...

    async def list(self) -> list[Project]: ...

    async def delete(self, project_id: uuid.UUID) -> None: ...
```

```python
# backend/app/domain/projects/errors.py
"""Domain-level errors for the project registry. The application layer raises these after
translating a database constraint violation (unique name, or a connection's foreign key) — this
module itself stays framework-agnostic, no SQLAlchemy/asyncpg imports.
"""

from __future__ import annotations

import uuid


class ProjectNameTakenError(Exception):
    """Raised creating a project whose name already exists in the registry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"a project named '{name}' already exists")
        self.name = name


class ProjectInUseError(Exception):
    """Raised deleting a project that's still referenced by at least one connection."""

    def __init__(self, project_id: uuid.UUID) -> None:
        super().__init__(f"project {project_id} is still referenced by one or more connections")
        self.project_id = project_id


class UnknownProjectError(Exception):
    """Raised creating or updating a connection whose `project` isn't in the registry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"unknown project '{name}' — add it in the Projects registry first")
        self.name = name
```

Create the empty package marker too:

```bash
touch backend/app/domain/projects/__init__.py
```

- [ ] **Step 4: Add the `ProjectRow` ORM table, and declare the FK on the two existing tables**

In `backend/app/infrastructure/db/orm.py`, insert this class immediately before
`class CloudConnectionRow(Base):` (around line 139):

```python
class ProjectRow(Base):
    """The shared registry of project names — see `.claude/specs/2026-08-23-project-registry-design.md`."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = _utcnow_column()
```

The migration (Step 5) adds the FK to the real dev/production database via `ALTER TABLE`, but every
test fixture in this codebase builds its schema fresh with `Base.metadata.create_all()` instead of
running Alembic — that only picks up constraints the ORM model itself declares (see
`ChatSessionRow.incident_id`'s `ForeignKey("incidents.id", ondelete="CASCADE")` for the existing
precedent). Without also declaring it here, every `create_all()`-based test fixture would build a
schema **without** the FK, silently defeating every test in this plan that depends on it being
enforced. So also change the existing `project` column definitions on both tables:

In `CloudConnectionRow` (currently `project: Mapped[str] = mapped_column(Text, nullable=False)`):

```python
    project: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.name", onupdate="CASCADE", ondelete="RESTRICT"), nullable=False
    )
```

In `AdoConnectionRow` (currently
`project: Mapped[str] = mapped_column(Text, nullable=False, unique=True)` — keep the existing
`unique=True`, it's unrelated to this FK):

```python
    project: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.name", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False, unique=True,
    )
```

`ForeignKey` is already imported at the top of `orm.py` (used by several other tables already).
The string target `"projects.name"` resolves lazily against `Base.metadata` the first time any
mapper is configured, so `ProjectRow`'s class not being defined until a few lines above this point
in the file is not an ordering hazard (unlike the `Depends(get_encryptor)` function-default-value
issue hit earlier this session in `deps.py` — that was a Python argument-evaluation-order rule, not
how SQLAlchemy resolves FK targets).

- [ ] **Step 5: Write the migration**

```python
# backend/migrations/versions/0013_projects.py
"""add projects registry, FK from cloud_connections/ado_connections.project

Revision ID: 0013_projects
Revises: 0012_ado_connections
Create Date: 2026-08-23

A shared registry of project names (`.claude/specs/2026-08-23-project-registry-design.md`).
`cloud_connections.project`/`ado_connections.project` stay TEXT columns (avoids reworking every
existing string-based lookup, e.g. `AdoConnectionRepository.get_by_project(incident.service)`) but
gain a database-level FK to `projects.name` so a typo or unregistered project name can no longer be
saved. The backfill runs before the FK is added so existing rows (EVP, rxdevs, ...) don't violate
the new constraint.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_projects"
down_revision: Union[str, None] = "0012_ado_connections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.execute(
        "INSERT INTO projects (id, name, created_at) "
        "SELECT gen_random_uuid(), distinct_project.name, now() FROM ("
        "  SELECT DISTINCT project AS name FROM cloud_connections "
        "  UNION "
        "  SELECT DISTINCT project AS name FROM ado_connections"
        ") AS distinct_project"
    )
    op.create_foreign_key(
        "fk_cloud_connections_project_projects",
        "cloud_connections", "projects",
        ["project"], ["name"],
        onupdate="CASCADE", ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ado_connections_project_projects",
        "ado_connections", "projects",
        ["project"], ["name"],
        onupdate="CASCADE", ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_ado_connections_project_projects", "ado_connections", type_="foreignkey")
    op.drop_constraint("fk_cloud_connections_project_projects", "cloud_connections", type_="foreignkey")
    op.drop_table("projects")
```

- [ ] **Step 6: Write the mapper**

In `backend/app/infrastructure/db/repositories/mappers.py`, add `Project`/`ProjectRow` to the
existing import blocks:

```python
from app.domain.projects.entities import Project
```

(add alongside the other `app.domain.*.entities` imports) and

```python
    ProjectRow,
```

(add alphabetically into the existing `from app.infrastructure.db.orm import (...)` block). Then
add the mapper function anywhere in the file (e.g. right after `ado_connection_to_domain`):

```python
def project_to_domain(row: ProjectRow) -> Project:
    return Project(id=row.id, name=row.name, created_at=row.created_at)
```

- [ ] **Step 7: Write the repository**

```python
# backend/app/infrastructure/db/repositories/projects.py
"""SQLAlchemy repository for the shared project registry (implements the port in
domain/projects/ports.py). Same convention as infrastructure/db/repositories/ado_connections.py.

Does not translate IntegrityError into a domain error itself — that happens one layer up, in
app/application/projects/manage.py (ManageProjects), matching where ManageCloudConnections and
ManageAdoConnections do the same translation for the new FK violation.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.projects.entities import Project
from app.infrastructure.db.orm import ProjectRow
from app.infrastructure.db.repositories.mappers import project_to_domain


class SqlAlchemyProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, project: Project) -> Project:
        row = ProjectRow(name=project.name)
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return project_to_domain(row)

    async def get(self, project_id: uuid.UUID) -> Project | None:
        row = await self._s.get(ProjectRow, project_id)
        return project_to_domain(row) if row else None

    async def list(self) -> list[Project]:
        rows = (await self._s.execute(select(ProjectRow))).scalars().all()
        return [project_to_domain(row) for row in rows]

    async def delete(self, project_id: uuid.UUID) -> None:
        row = await self._s.get(ProjectRow, project_id)
        if row is not None:
            await self._s.delete(row)
            await self._s.flush()
```

Then in `backend/app/infrastructure/db/repositories/__init__.py`, add:

```python
from app.infrastructure.db.repositories.projects import SqlAlchemyProjectRepository
```

(alphabetically among the existing imports) and add `"SqlAlchemyProjectRepository"` to `__all__`.

- [ ] **Step 8: Apply the migration and run the test to verify it passes**

Run:
```bash
cd backend
uv run alembic upgrade head
uv run pytest tests/test_project_repository.py -v
```
Expected: PASS (all 5 tests).

- [ ] **Step 9: Commit**

```bash
git add backend/app/domain/projects backend/app/infrastructure/db/orm.py \
  backend/app/infrastructure/db/repositories/mappers.py \
  backend/app/infrastructure/db/repositories/projects.py \
  backend/app/infrastructure/db/repositories/__init__.py \
  backend/migrations/versions/0013_projects.py \
  backend/tests/test_project_repository.py
git commit -m "feat: add project-registry domain, migration, and repository"
```

---

### Task 2: ManageProjects application use case

**Files:**
- Create: `backend/app/application/projects/__init__.py` (empty)
- Create: `backend/app/application/projects/manage.py`
- Test: `backend/tests/test_manage_projects.py`

**Interfaces:**
- Consumes: `Project`, `ProjectRepository` (Task 1); `UnitOfWork` Protocol
  (`app.domain.shared.UnitOfWork`, already has `commit()`/`rollback()` — `rollback()` was added
  earlier this session in `backend/app/domain/shared.py` for the `poll_alarms.py` fix); domain
  errors `ProjectNameTakenError`, `ProjectInUseError` (Task 1).
- Produces: `ManageProjects(projects: ProjectRepository, uow: UnitOfWork)` with
  `create(name: str) -> Project`, `list() -> list[Project]`, `delete(project_id: uuid.UUID) -> None`
  (raises `ValueError` if the id doesn't exist, `ProjectInUseError` if it's referenced).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_manage_projects.py
"""Unit tests for ManageProjects — no DB, no network."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.application.projects.manage import ManageProjects
from app.domain.projects.entities import Project
from app.domain.projects.errors import ProjectInUseError, ProjectNameTakenError

pytestmark = pytest.mark.asyncio


class FakeRepo:
    def __init__(self):
        self.rows = {}
        self.raise_on_add = None
        self.raise_on_delete = None

    async def add(self, project):
        if self.raise_on_add:
            raise self.raise_on_add
        project.id = uuid.uuid4()
        self.rows[project.id] = project
        return project

    async def get(self, project_id):
        return self.rows.get(project_id)

    async def list(self):
        return list(self.rows.values())

    async def delete(self, project_id):
        if self.raise_on_delete:
            raise self.raise_on_delete
        self.rows.pop(project_id, None)


class FakeUnitOfWork:
    def __init__(self):
        self.rolled_back = False

    async def commit(self):
        pass

    async def rollback(self):
        self.rolled_back = True


def _manager(repo=None, uow=None):
    return ManageProjects(projects=repo or FakeRepo(), uow=uow or FakeUnitOfWork())


async def test_create_returns_the_project():
    manager = _manager()
    project = await manager.create("EVP")
    assert project.name == "EVP"
    assert project.id is not None


async def test_create_duplicate_name_raises_project_name_taken():
    repo = FakeRepo()
    repo.raise_on_add = IntegrityError("stmt", {}, Exception("dup"))
    uow = FakeUnitOfWork()
    manager = _manager(repo=repo, uow=uow)
    with pytest.raises(ProjectNameTakenError):
        await manager.create("EVP")
    assert uow.rolled_back is True


async def test_delete_unknown_id_raises_value_error():
    manager = _manager()
    with pytest.raises(ValueError):
        await manager.delete(uuid.uuid4())


async def test_delete_referenced_project_raises_project_in_use():
    repo = FakeRepo()
    project = await repo.add(Project(name="EVP"))
    repo.raise_on_delete = IntegrityError("stmt", {}, Exception("fk violation"))
    uow = FakeUnitOfWork()
    manager = _manager(repo=repo, uow=uow)
    with pytest.raises(ProjectInUseError):
        await manager.delete(project.id)
    assert uow.rolled_back is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_manage_projects.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.application.projects'`.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/application/projects/manage.py
"""ManageProjects: CRUD for the shared project registry that CloudConnection/AdoConnection (and
future per-project connection types) reference. Translates the database-level constraint
violations (unique name on create, FK-from-a-connection on delete) into domain errors here — same
convention as ManageCloudConnections/ManageAdoConnections translating the new FK violation on
their own `create`/`update` (see `.claude/specs/2026-08-23-project-registry-design.md`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.domain.projects.entities import Project
from app.domain.projects.errors import ProjectInUseError, ProjectNameTakenError
from app.domain.projects.ports import ProjectRepository
from app.domain.shared import UnitOfWork


@dataclass
class ManageProjects:
    projects: ProjectRepository
    uow: UnitOfWork

    async def create(self, name: str) -> Project:
        try:
            project = await self.projects.add(Project(name=name))
        except IntegrityError as exc:
            await self.uow.rollback()
            raise ProjectNameTakenError(name) from exc
        await self.uow.commit()
        return project

    async def list(self) -> list[Project]:
        return await self.projects.list()

    async def delete(self, project_id: uuid.UUID) -> None:
        existing = await self.projects.get(project_id)
        if existing is None:
            raise ValueError(f"project {project_id} not found")
        try:
            await self.projects.delete(project_id)
        except IntegrityError as exc:
            await self.uow.rollback()
            raise ProjectInUseError(project_id) from exc
        await self.uow.commit()
```

Create the empty package marker too:

```bash
touch backend/app/application/projects/__init__.py
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_manage_projects.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/application/projects backend/tests/test_manage_projects.py
git commit -m "feat: add ManageProjects application use case"
```

---

### Task 3: `/api/projects` HTTP surface

**Files:**
- Create: `backend/app/interface/http/dto/request/project.py`
- Create: `backend/app/interface/http/dto/response/project.py`
- Create: `backend/app/interface/http/dto/mappers/project.py`
- Modify: `backend/app/interface/http/dto/request/__init__.py`
- Modify: `backend/app/interface/http/dto/response/__init__.py`
- Modify: `backend/app/interface/http/dto/mappers/__init__.py`
- Modify: `backend/app/interface/http/deps.py`
- Create: `backend/app/interface/http/projects.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_projects_http.py`

**Interfaces:**
- Consumes: `ManageProjects` (Task 2); `ProjectNameTakenError`/`ProjectInUseError` (Task 1);
  `SqlAlchemyProjectRepository` (Task 1); `require_admin`, `get_session`, `get_unit_of_work` (all
  already defined in `deps.py`).
- Produces: `GET/POST /api/projects`, `DELETE /api/projects/{id}`; `get_project_repository`,
  `get_manage_projects` deps functions for Task 4 to reuse if needed.

- [ ] **Step 1: Write the failing HTTP test**

```python
# backend/tests/test_projects_http.py
"""End-to-end HTTP tests for /api/projects against real Postgres.

Overrides get_encryptor and require_admin (same pattern as test_ado_connections_http.py, even
though projects have no secrets — kept consistent with the other Settings-CRUD test fixtures).
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.db.orm import AdoConnectionRow, Base, CloudConnectionRow, ProjectRow
from app.interface.http.deps import get_session, require_admin
from app.main import app

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
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
        pytest.skip(f"Postgres not reachable for HTTP test: {exc}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        # See test_project_repository.py's fixture note: `projects` is shared with
        # cloud_connections/ado_connections across test files, so delete both referencing
        # tables before `projects` itself to avoid a leftover row from another file colliding
        # with (or blocking the delete of) the names this file uses.
        await s.execute(delete(CloudConnectionRow))
        await s.execute(delete(AdoConnectionRow))
        await s.execute(delete(ProjectRow))
        await s.commit()

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[require_admin] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_create_and_list(client):
    r = await client.post("/api/projects", json={"name": "EVP"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "EVP"
    assert "id" in body and "created_at" in body

    r = await client.get("/api/projects")
    assert r.status_code == 200
    assert [p["name"] for p in r.json()] == ["EVP"]


async def test_create_blank_name_is_422(client):
    r = await client.post("/api/projects", json={"name": "   "})
    assert r.status_code == 422


async def test_create_duplicate_name_is_409(client):
    await client.post("/api/projects", json={"name": "EVP"})
    r = await client.post("/api/projects", json={"name": "EVP"})
    assert r.status_code == 409


async def test_delete_unreferenced_project(client):
    r = await client.post("/api/projects", json={"name": "EVP"})
    project_id = r.json()["id"]

    r = await client.delete(f"/api/projects/{project_id}")
    assert r.status_code == 204

    r = await client.get("/api/projects")
    assert r.json() == []


async def test_delete_unknown_id_is_404(client):
    r = await client.delete("/api/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_delete_referenced_project_is_409(client, monkeypatch):
    r = await client.post("/api/projects", json={"name": "EVP"})
    project_id = r.json()["id"]

    r = await client.post(
        "/api/cloud-connections",
        json={
            "project": "EVP", "env": "prod", "region": "us-east-1",
            "auth_type": "sso", "sso_profile_name": "p",
        },
    )
    assert r.status_code == 201, r.text

    r = await client.delete(f"/api/projects/{project_id}")
    assert r.status_code == 409
```

Note: `test_delete_referenced_project_is_409` creates a real `cloud-connections` row through its own
endpoint. `POST /api/cloud-connections` only calls `ManageCloudConnections.create()`, which never
touches the injected `AlarmFetcher` (that's only used by the poll/test endpoints) — so no fetcher
override is needed here, and this fixture doesn't need `require_admin` overridden either beyond
what's already set above.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_projects_http.py -v`
Expected: FAIL — `404 Not Found` on `POST /api/projects` (no router registered yet).

- [ ] **Step 3: Write the DTOs**

```python
# backend/app/interface/http/dto/request/project.py
"""Project-registry request DTOs (the parse-first boundary)."""

from __future__ import annotations

from pydantic import BaseModel


class ProjectCreateRequest(BaseModel):
    """`POST /api/projects` body."""

    name: str
```

```python
# backend/app/interface/http/dto/response/project.py
"""Project-registry response DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ProjectOut(BaseModel):
    """One row in `GET /api/projects`."""

    id: uuid.UUID
    name: str
    created_at: datetime
```

```python
# backend/app/interface/http/dto/mappers/project.py
"""Mappers: Project domain entity -> response DTO."""

from __future__ import annotations

from app.domain.projects.entities import Project
from app.interface.http.dto.response.project import ProjectOut


def project_out(project: Project) -> ProjectOut:
    return ProjectOut(id=project.id, name=project.name, created_at=project.created_at)
```

- [ ] **Step 4: Wire the DTOs into the `__init__.py` re-exports**

In `backend/app/interface/http/dto/request/__init__.py`, add
`from app.interface.http.dto.request.project import ProjectCreateRequest` and `"ProjectCreateRequest"`
to `__all__`.

In `backend/app/interface/http/dto/response/__init__.py`, add
`from app.interface.http.dto.response.project import ProjectOut` and `"ProjectOut"` to `__all__`.

In `backend/app/interface/http/dto/mappers/__init__.py`, add
`from app.interface.http.dto.mappers.project import project_out` and `"project_out"` to `__all__`.

- [ ] **Step 5: Wire the dependencies**

In `backend/app/interface/http/deps.py`, add these imports near the existing ADO-connection ones:

```python
from app.application.projects.manage import ManageProjects
from app.domain.projects.ports import ProjectRepository
```

and add `SqlAlchemyProjectRepository` to the existing
`from app.infrastructure.db.repositories import (...)` import block.

Then add these two functions anywhere after `get_unit_of_work` is defined (e.g. right after the
`get_ado_ticket_client_factory` block) — neither depends on `get_encryptor`, so there's no
ordering hazard like the one hit earlier this session with the ADO-connection deps:

```python
def get_project_repository(
    session: AsyncSession = Depends(get_session),
) -> ProjectRepository:
    return SqlAlchemyProjectRepository(session)


def get_manage_projects(
    projects: ProjectRepository = Depends(get_project_repository),
    uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work),
) -> ManageProjects:
    return ManageProjects(projects=projects, uow=uow)
```

- [ ] **Step 6: Write the router**

```python
# backend/app/interface/http/projects.py
"""Project-registry HTTP controller: CRUD for the shared project names that CloudConnection/
AdoConnection reference. Admin-gated, same shape as ado_connections.py."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.projects.manage import ManageProjects
from app.domain.projects.errors import ProjectInUseError, ProjectNameTakenError
from app.interface.http.deps import get_manage_projects, require_admin
from app.interface.http.dto import mappers
from app.interface.http.dto.request import ProjectCreateRequest
from app.interface.http.dto.response import ProjectOut

router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(require_admin)])


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateRequest,
    manager: ManageProjects = Depends(get_manage_projects),
) -> ProjectOut:
    if not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name must not be blank"
        )
    try:
        project = await manager.create(body.name)
    except ProjectNameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return mappers.project_out(project)


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    manager: ManageProjects = Depends(get_manage_projects),
) -> list[ProjectOut]:
    projects = await manager.list()
    return [mappers.project_out(p) for p in projects]


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    manager: ManageProjects = Depends(get_manage_projects),
) -> None:
    try:
        await manager.delete(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProjectInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
```

- [ ] **Step 7: Register the router**

In `backend/app/main.py`, add `from app.interface.http.projects import router as projects_router`
alongside the other router imports, and `app.include_router(projects_router)` alongside the other
`include_router` calls.

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_projects_http.py -v`
Expected: PASS (6 tests). If `test_delete_referenced_project_is_409` fails on the `POST
/api/cloud-connections` call rather than the final assertion, apply the fetcher-override fix noted
in Step 1 and re-run.

- [ ] **Step 9: Run the full backend test suite and lint**

Run:
```bash
cd backend
uv run pytest -q
uv run ruff check app/ tests/
```
Expected: all green, `ruff check` returns `[]`.

- [ ] **Step 10: Commit**

```bash
git add backend/app/interface/http/dto backend/app/interface/http/deps.py \
  backend/app/interface/http/projects.py backend/app/main.py backend/tests/test_projects_http.py
git commit -m "feat: add /api/projects CRUD endpoints"
```

---

### Task 4: Reject unknown projects cleanly on connection create/update

**Files:**
- Modify: `backend/app/application/cloud_connections/manage.py`
- Modify: `backend/app/application/ado_connections/manage.py`
- Modify: `backend/app/interface/http/cloud_connections.py`
- Modify: `backend/app/interface/http/ado_connections.py`
- Modify: `backend/tests/test_manage_cloud_connections.py`
- Modify: `backend/tests/test_cloud_connections_http.py`
- Modify: `backend/tests/test_ado_connections_http.py`

**Interfaces:**
- Consumes: `UnknownProjectError` (Task 1); `UnitOfWork.rollback()` (already exists, added earlier
  this session for the `poll_alarms.py` fix).
- Produces: `ManageCloudConnections.create`/`.update` and `ManageAdoConnections.create`/`.update`
  now raise `UnknownProjectError` instead of an uncaught `IntegrityError`; both HTTP routers map it
  to `422`.

- [ ] **Step 1: Write the failing unit test for `ManageCloudConnections`**

In `backend/tests/test_manage_cloud_connections.py`, modify `FakeRepo` to support a
raise-on-cue hook, and `FakeUnitOfWork` to track a rollback call — apply this diff:

```python
class FakeRepo:
    def __init__(self):
        self.rows = {}
        self.raise_on_add = None

    async def add(self, connection):
        if self.raise_on_add:
            raise self.raise_on_add
        connection.id = "generated-id"
        self.rows[connection.id] = connection
        return connection

    async def get(self, connection_id):
        return self.rows.get(connection_id)

    async def list(self):
        return list(self.rows.values())

    async def delete(self, connection_id):
        self.rows.pop(connection_id, None)
```

```python
class FakeUnitOfWork:
    def __init__(self):
        self.rolled_back = False

    async def commit(self):
        pass

    async def rollback(self):
        self.rolled_back = True
```

```python
def _manager(fetcher=None, repo=None, uow=None):
    return ManageCloudConnections(
        connections=repo or FakeRepo(), encryptor=Encryptor(_KEY),
        fetcher=fetcher or FakeFetcher(), uow=uow or FakeUnitOfWork(),
    )
```

(`_manager`'s signature grows two optional keyword args — every existing call site,
e.g. `_manager()` and `_manager(fetcher=FakeFetcher(should_fail=True))`, keeps working unchanged.)

Then add this test at the end of the file:

```python
async def test_create_unknown_project_raises_unknown_project_error():
    from sqlalchemy.exc import IntegrityError

    from app.domain.projects.errors import UnknownProjectError

    repo = FakeRepo()
    repo.raise_on_add = IntegrityError("stmt", {}, Exception("fk violation"))
    uow = FakeUnitOfWork()
    manager = _manager(repo=repo, uow=uow)
    with pytest.raises(UnknownProjectError):
        await manager.create(
            project="NOPE", env="prod", region="us-east-1", auth_type="sso",
            sso_profile_name="p",
        )
    assert uow.rolled_back is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_manage_cloud_connections.py -v`
Expected: FAIL — `IntegrityError` propagates unchanged (no `UnknownProjectError` raised yet).

- [ ] **Step 3: Update `ManageCloudConnections`**

In `backend/app/application/cloud_connections/manage.py`, add these imports:

```python
from sqlalchemy.exc import IntegrityError

from app.domain.projects.errors import UnknownProjectError
```

Wrap the `add()` call in `create()`:

```python
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
        try:
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
        except IntegrityError as exc:
            await self.uow.rollback()
            raise UnknownProjectError(project) from exc
        await self.uow.commit()
        return connection
```

And the `self.connections.update(updated)` call in `update()`:

```python
        try:
            result = await self.connections.update(updated)
        except IntegrityError as exc:
            await self.uow.rollback()
            raise UnknownProjectError(project) from exc
        await self.uow.commit()
        return result
```

(replacing the existing two lines `result = await self.connections.update(updated)` /
`await self.uow.commit()` at the end of `update()`).

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_manage_cloud_connections.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Repeat for `ManageAdoConnections` — write the failing HTTP test**

`ManageAdoConnections` has no dedicated unit-test file (only HTTP end-to-end coverage exists for
it today) — add the new case there instead of inventing a new fake-repo file. In
`backend/tests/test_ado_connections_http.py`, add:

```python
async def test_create_unknown_project_is_422(client):
    r = await client.post(
        "/api/ado-connections",
        json={"project": "NOPE", "org": "my-org", "ado_project": "EVP-Board", "pat": "secret"},
    )
    assert r.status_code == 422
```

This test also needs the fixture to seed a `projects` row for every `project` value the *other*
tests in this file already use (`EVP`, `rxdevs`), since the new FK now rejects any `project` value
that isn't registered — without this, every other test in the file starts failing once the FK
exists. `projects` is shared with `cloud_connections` too (Task 4 Step 10 seeds it there
independently), so reset it unconditionally here rather than only adding to it — a stray row left
over from a different test file must never collide with (or block re-seeding) the names this file
uses. Replace the fixture's existing cleanup block:

```python
    async with maker() as s:
        await s.execute(delete(AdoConnectionRow))
        await s.commit()
```

with:

```python
    async with maker() as s:
        await s.execute(delete(AdoConnectionRow))
        await s.execute(delete(CloudConnectionRow))
        await s.execute(delete(ProjectRow))
        await s.commit()
        s.add_all([ProjectRow(id=uuid.uuid4(), name="EVP"), ProjectRow(id=uuid.uuid4(), name="rxdevs")])
        await s.commit()
```

(add `import uuid` and change the existing `from app.infrastructure.db.orm import AdoConnectionRow,
Base` import to also bring in `CloudConnectionRow, ProjectRow`).

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_ado_connections_http.py -v`
Expected: FAIL — `test_create_unknown_project_is_422` gets `500` instead of `422` (uncaught
`IntegrityError`).

- [ ] **Step 7: Update `ManageAdoConnections`**

In `backend/app/application/ado_connections/manage.py`, add these imports:

```python
import asyncpg
from sqlalchemy.exc import IntegrityError

from app.domain.projects.errors import UnknownProjectError
```

Unlike `cloud_connections` (which has no other unique constraint on `project`),
`ado_connections.project` is itself `UNIQUE` (one ADO destination per project) — so an
`IntegrityError` here can mean either "unknown project" (the new FK) or "this project already has
an ADO connection" (the pre-existing unique constraint, unrelated and out of scope for this plan —
leave that case exactly as it behaves today). Disambiguate by inspecting `exc.orig`:

```python
    async def create(
        self, *, project: str, org: str, ado_project: str, pat: str, work_item_type: str = "Bug"
    ) -> AdoConnection:
        try:
            connection = await self.connections.add(
                AdoConnection(
                    project=project,
                    org=org,
                    ado_project=ado_project,
                    encrypted_pat=self.encryptor.encrypt(pat),
                    work_item_type=work_item_type,
                )
            )
        except IntegrityError as exc:
            await self.uow.rollback()
            if isinstance(exc.orig, asyncpg.exceptions.ForeignKeyViolationError):
                raise UnknownProjectError(project) from exc
            raise
        await self.uow.commit()
        return connection
```

And the `self.connections.update(updated)` call in `update()`:

```python
        try:
            result = await self.connections.update(updated)
        except IntegrityError as exc:
            await self.uow.rollback()
            if isinstance(exc.orig, asyncpg.exceptions.ForeignKeyViolationError):
                raise UnknownProjectError(project) from exc
            raise
        await self.uow.commit()
        return result
```

(replacing the existing two lines `result = await self.connections.update(updated)` /
`await self.uow.commit()` at the end of `update()`).

- [ ] **Step 8: Map `UnknownProjectError` to 422 in both HTTP controllers**

In `backend/app/interface/http/cloud_connections.py`, add the import
`from app.domain.projects.errors import UnknownProjectError`, then in `create_connection` wrap the
`manager.create(...)` call:

```python
    try:
        connection = await manager.create(
            project=body.project, env=body.env, region=body.region, auth_type=body.auth_type,
            sso_profile_name=body.sso_profile_name, access_key_id=body.access_key_id,
            secret_access_key=body.secret_access_key,
        )
    except UnknownProjectError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return mappers.cloud_connection_out(connection)
```

and add the same `except UnknownProjectError` clause to `update_connection`'s existing
`try`/`except ValueError` block (as an additional `except` clause on the same `try`).

Apply the identical two changes to `backend/app/interface/http/ado_connections.py` (import
`UnknownProjectError`, wrap `create_connection`'s `manager.create(...)` call, add the `except
UnknownProjectError` clause to `update_connection`'s existing `try`).

- [ ] **Step 9: Run both test files to verify they pass**

Run:
```bash
cd backend
uv run pytest tests/test_manage_cloud_connections.py tests/test_cloud_connections_http.py \
  tests/test_ado_connections_http.py -v
```
Expected: all PASS. `test_cloud_connections_http.py` will fail at this point unless its fixture is
also updated (Step 10) — the FK now rejects its existing `GCM`/`EVP` fixture data.

- [ ] **Step 10: Seed projects in `test_cloud_connections_http.py`'s fixture**

Add `import uuid` and `AdoConnectionRow, ProjectRow` to the existing
`from app.infrastructure.db.orm import (...)` block. Its fixture currently reads:

```python
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(TrackedAlarmRow))
        await s.execute(delete(CloudConnectionRow))
        await s.commit()
```

Replace it with (same reasoning as Task 4 Step 5 — `projects` is shared across test files via the
new FK, so reset it unconditionally rather than only adding to it):

```python
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(TrackedAlarmRow))
        await s.execute(delete(CloudConnectionRow))
        await s.execute(delete(AdoConnectionRow))
        await s.execute(delete(ProjectRow))
        await s.commit()
        s.add_all([ProjectRow(id=uuid.uuid4(), name="GCM"), ProjectRow(id=uuid.uuid4(), name="EVP")])
        await s.commit()
```

- [ ] **Step 11: Run the full backend test suite and lint**

Run:
```bash
cd backend
uv run pytest -q
uv run ruff check app/ tests/
```
Expected: all green, `ruff check` returns `[]`.

- [ ] **Step 12: Commit**

```bash
git add backend/app/application/cloud_connections/manage.py \
  backend/app/application/ado_connections/manage.py \
  backend/app/interface/http/cloud_connections.py backend/app/interface/http/ado_connections.py \
  backend/tests/test_manage_cloud_connections.py backend/tests/test_cloud_connections_http.py \
  backend/tests/test_ado_connections_http.py
git commit -m "fix: reject an unknown project cleanly (422) instead of 500ing on the new FK"
```

---

### Task 5: Frontend Project registry (types, component, Settings wiring)

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Create: `frontend/src/features/settings/ProjectRegistry.tsx`
- Modify: `frontend/src/pages/Settings.tsx`

**Interfaces:**
- Produces: `Project { id: string; name: string; created_at: string }`,
  `ProjectCreate { name: string }` (`frontend/src/lib/types.ts`); `ProjectRegistry` component with
  props `{ projects: Project[]; onCreated: (p: Project) => void; onDeleted: (id: string) => void }`.
  `SettingsContent` gains a `projects: Project[]` state that Task 6 passes down to
  `CloudConnectionForm`/`AdoConnectionForm`.

- [ ] **Step 1: Add the types**

In `frontend/src/lib/types.ts`, add near the existing `AdoConnection`/`AdoConnectionCreate`
interfaces:

```typescript
export interface Project {
  id: string
  name: string
  created_at: string
}

export interface ProjectCreate {
  name: string
}
```

- [ ] **Step 2: Write the `ProjectRegistry` component**

```tsx
// frontend/src/features/settings/ProjectRegistry.tsx
import { useState } from 'react'
import { api, errText } from '../../lib/api'
import type { Project } from '../../lib/types'
import { Button } from '../../components/ui/Button'

export function ProjectRegistry({
  projects,
  onCreated,
  onDeleted,
}: {
  projects: Project[]
  onCreated: (p: Project) => void
  onDeleted: (id: string) => void
}) {
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const add = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy('new')
    try {
      const created = await api.post<Project>('/api/projects', { name })
      onCreated(created)
      setName('')
    } catch (err) {
      setError(errText(err))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (id: string) => {
    setError(null)
    setBusy(id)
    try {
      await api.del(`/api/projects/${id}`)
      onDeleted(id)
    } catch (err) {
      setError(errText(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-hair bg-surface p-4">
      <h3 className="text-sm font-semibold text-muted">Projects</h3>
      <form onSubmit={add} className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. EVP"
          className="flex-1 rounded-lg border border-hair bg-plane p-2 text-sm text-ink outline-none focus:border-accent"
          required
        />
        <Button type="submit" disabled={busy === 'new'}>
          {busy === 'new' ? 'Adding…' : 'Add'}
        </Button>
      </form>
      {error && <p className="text-sm text-sev-critical">{error}</p>}
      {projects.length === 0 ? (
        <p className="text-sm text-muted">No projects yet.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {projects.map((p) => (
            <li
              key={p.id}
              className="flex items-center gap-2 rounded-full border border-hair bg-plane px-3 py-1 text-sm text-ink"
            >
              {p.name}
              <button
                type="button"
                disabled={busy === p.id}
                onClick={() => remove(p.id)}
                className="text-muted hover:text-sev-critical"
                aria-label={`Delete ${p.name}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Wire it into `Settings.tsx`**

In `frontend/src/pages/Settings.tsx`, add the import:

```tsx
import { ProjectRegistry } from '../features/settings/ProjectRegistry'
```

and add `Project` to the existing `import type { AdoConnection, CloudConnection, PollResult,
PollSchedule } from '../lib/types'` line.

Add state and a loader, alongside the existing `adoConnections`/`editingAdo` state:

```tsx
  const [projects, setProjects] = useState<Project[]>([])
```

```tsx
  const loadProjects = async () => {
    try {
      setProjects(await api.get<Project[]>('/api/projects'))
    } catch {
      setProjects([]) // non-critical — forms just show "No projects yet" until this loads
    }
  }
```

Add `loadProjects()` to the mount effect:

```tsx
  useEffect(() => {
    load()
    loadSchedule()
    loadAdo()
    loadProjects()
  }, [])
```

Render `<ProjectRegistry>` as the first item inside the returned `<div className="animate-in ...">`,
above `<ClaudeTokenForm />`:

```tsx
        <ProjectRegistry
          projects={projects}
          onCreated={(p) => setProjects((prev) => [...prev, p])}
          onDeleted={(id) => setProjects((prev) => prev.filter((p) => p.id !== id))}
        />

        <ClaudeTokenForm />
```

- [ ] **Step 4: Verify the build**

Run: `cd frontend && npm run build`
Expected: clean build, no TypeScript errors (there's no automated frontend test suite in this
project — a clean `tsc -b && vite build` is the correctness gate, per `CLAUDE.md`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/features/settings/ProjectRegistry.tsx \
  frontend/src/pages/Settings.tsx
git commit -m "feat: add Project registry UI to Settings"
```

---

### Task 6: Wire the Project dropdown into CloudConnectionForm and AdoConnectionForm

**Files:**
- Modify: `frontend/src/features/settings/CloudConnectionForm.tsx`
- Modify: `frontend/src/features/settings/AdoConnectionForm.tsx`
- Modify: `frontend/src/pages/Settings.tsx`

**Interfaces:**
- Consumes: `projects: Project[]` state from `Settings.tsx` (Task 5).
- Produces: both forms accept a `projects: Project[]` prop and no longer accept free-text project
  input — `KNOWN_PROJECTS` is removed from both files. `CloudConnectionForm.tsx` keeps
  `SelectOrOtherField` for its `Env`/`Region` fields (those aren't part of this registry) and keeps
  its own `KNOWN_ENVS`/`KNOWN_REGIONS` constants unchanged.

- [ ] **Step 1: Update `CloudConnectionForm.tsx`**

Remove the `KNOWN_PROJECTS` constant (line 7) and the `SelectOrOtherField` import stays (still
used for Env/Region). Add a `Project` type import:

```tsx
import type { CloudConnection, CloudConnectionCreate, Project } from '../../lib/types'
```

Add a `projects` prop to the component signature:

```tsx
export function CloudConnectionForm({
  editing,
  projects,
  onCreated,
  onUpdated,
  onCancelEdit,
}: {
  /** When set, the form edits this connection (PATCH) instead of creating a new one (POST). */
  editing?: CloudConnection | null
  projects: Project[]
  onCreated?: (c: CloudConnection) => void
  onUpdated?: (c: CloudConnection) => void
  onCancelEdit?: () => void
}) {
```

Change the initial `project` state (previously seeded from `KNOWN_PROJECTS[0]`) to an empty string,
seeded from `projects` once loaded:

```tsx
  const [project, setProject] = useState('')
```

Add an effect that defaults `project` to the first available project once `projects` loads (only
when not editing — editing already sets `project` from `editing.project` via the existing effect):

```tsx
  useEffect(() => {
    if (editing || project || projects.length === 0) return
    setProject(projects[0].name)
  }, [projects, editing, project])
```

Replace the Project `SelectOrOtherField` with a plain `<select>`:

```tsx
      <div className="flex gap-3">
        {projects.length === 0 ? (
          <p className="flex-1 text-sm text-muted">No projects yet — add one above.</p>
        ) : (
          <label className="flex-1 text-sm text-ink-2">
            Project
            <select
              value={project}
              onChange={(e) => setProject(e.target.value)}
              className={inputCls}
              required
            >
              {projects.map((p) => (
                <option key={p.id} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <SelectOrOtherField label="Env" options={KNOWN_ENVS} value={env} onChange={setEnv} />
        <SelectOrOtherField label="Region" options={KNOWN_REGIONS} value={region} onChange={setRegion} />
      </div>
```

- [ ] **Step 2: Update `AdoConnectionForm.tsx`**

Remove the `KNOWN_PROJECTS` constant and the `SelectOrOtherField` import (this form has no other
`SelectOrOtherField` usage, so the import is dropped entirely here — unlike `CloudConnectionForm`).
Add a `Project` type import:

```tsx
import type { AdoConnection, AdoConnectionCreate, Project } from '../../lib/types'
```

Add a `projects` prop to the component signature (same shape as Task 6 Step 1):

```tsx
export function AdoConnectionForm({
  editing,
  projects,
  onCreated,
  onUpdated,
  onCancelEdit,
}: {
  editing?: AdoConnection | null
  projects: Project[]
  onCreated?: (c: AdoConnection) => void
  onUpdated?: (c: AdoConnection) => void
  onCancelEdit?: () => void
}) {
```

Change the initial `project` state and add the same defaulting effect as Step 1:

```tsx
  const [project, setProject] = useState('')
```

```tsx
  useEffect(() => {
    if (editing || project || projects.length === 0) return
    setProject(projects[0].name)
  }, [projects, editing, project])
```

Replace the `SelectOrOtherField` for Project with a plain `<select>` (matching the surrounding
`flex-1` layout of the ADO org / ADO project fields it sits beside):

```tsx
      <div className="flex gap-3">
        {projects.length === 0 ? (
          <p className="flex-1 text-sm text-muted">No projects yet — add one above.</p>
        ) : (
          <label className="flex-1 text-sm text-ink-2">
            Project
            <select
              value={project}
              onChange={(e) => setProject(e.target.value)}
              className={inputCls}
              required
            >
              {projects.map((p) => (
                <option key={p.id} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex-1 text-sm text-ink-2">
          ADO organization
          <input
            value={org}
            onChange={(e) => setOrg(e.target.value)}
            placeholder="my-org"
            className={inputCls}
            required
          />
        </label>
        <label className="flex-1 text-sm text-ink-2">
          ADO project name
          <input
            value={adoProject}
            onChange={(e) => setAdoProject(e.target.value)}
            placeholder="EVP-Board"
            className={inputCls}
            required
          />
        </label>
      </div>
```

- [ ] **Step 3: Pass `projects` from `Settings.tsx`**

In `frontend/src/pages/Settings.tsx`, add `projects={projects}` to both `<CloudConnectionForm>` and
`<AdoConnectionForm>` call sites.

- [ ] **Step 4: Verify the build**

Run: `cd frontend && npm run build`
Expected: clean build, no TypeScript errors.

- [ ] **Step 5: Manual smoke test**

Run: `docker compose up -d --build backend frontend`, then in the browser:
1. Open Settings — the "Projects" card shows the projects backfilled from the migration (`EVP`,
   `rxdevs`, `GCM`, ... whatever already existed in the dev database).
2. Add a new project name; confirm it appears in the list and in both connection forms' Project
   dropdown without a page reload.
3. Try adding a duplicate project name; confirm an inline error appears (409).
4. Try deleting a project that has a connection; confirm an inline error appears (409) and the
   project stays in the list.
5. Create an AWS or ADO connection using the dropdown; confirm it saves normally.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/settings/CloudConnectionForm.tsx \
  frontend/src/features/settings/AdoConnectionForm.tsx frontend/src/pages/Settings.tsx
git commit -m "feat: select project from the shared registry instead of free text"
```
