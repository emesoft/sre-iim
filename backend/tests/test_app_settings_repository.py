"""Integration tests for SqlAlchemyAppSettingsRepository against real Postgres.

Skipped when no database is reachable (same convention as test_documents_http.py).
"""

import os

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.db.orm import AppSettingRow, Base
from app.infrastructure.db.repositories.app_settings import SqlAlchemyAppSettingsRepository

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
        await s.execute(delete(AppSettingRow))
        await s.commit()
        yield s

    await engine.dispose()


async def test_get_returns_none_when_unset(session):
    repo = SqlAlchemyAppSettingsRepository(session)
    assert await repo.get("claude_cli_token") is None


async def test_set_then_get_round_trips(session):
    repo = SqlAlchemyAppSettingsRepository(session)
    await repo.set("claude_cli_token", "ciphertext-abc")
    await session.commit()
    assert await repo.get("claude_cli_token") == "ciphertext-abc"


async def test_set_upserts_an_existing_key(session):
    repo = SqlAlchemyAppSettingsRepository(session)
    await repo.set("claude_cli_token", "first")
    await session.commit()
    await repo.set("claude_cli_token", "second")
    await session.commit()
    assert await repo.get("claude_cli_token") == "second"
