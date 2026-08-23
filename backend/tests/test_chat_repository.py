"""Integration tests for SqlAlchemyChatRepository against real Postgres (iim_test)."""

import os
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.incidents.entities import ChatMessage
from app.infrastructure.db.orm import AnalysisRow, Base, ChatMessageRow, ChatSessionRow, IncidentRow
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
        await s.execute(delete(AnalysisRow))
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
