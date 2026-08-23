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
