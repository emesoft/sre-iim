"""HTTP tests for GET/POST /api/incidents/{id}/chat — overrides get_incident_chat with a fake
IncidentChat (no real subprocess) and get_session with a real Postgres session (iim_test)."""

import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.incidents.entities import ChatMessage
from app.domain.incidents.ports import ChatRepository
from app.domain.shared import UnitOfWork
from app.domain.users.entities import User
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.db.orm import Base, ChatMessageRow, ChatSessionRow, IncidentRow
from app.interface.http.deps import (
    get_chat_repository,
    get_current_user,
    get_incident_chat,
    get_session,
    get_unit_of_work,
)
from app.main import app

pytestmark = pytest.mark.asyncio

# POST .../chat is gated to admin/sre now that per-user auth exists — override get_current_user
# with a fake admin so this suite exercises the chat use case, not the role gate.
_ADMIN_USER = User(
    id=uuid.uuid4(),
    email="admin@test.local",
    password_hash="unused",
    role="admin",
    created_at=datetime.now(timezone.utc),
)

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://iim:iim@localhost:5432/iim"
)


class _FakeIncidentChat:
    """Persists only the assistant reply (via the real chat repo, so GET .../chat sees it) —
    skips the real IncidentChat's subprocess call to the Claude Code CLI entirely."""

    def __init__(self, chat: ChatRepository, uow: UnitOfWork) -> None:
        self._chat = chat
        self._uow = uow

    async def send_message(self, incident, message):
        reply = await self._chat.add_message(
            ChatMessage(
                incident_id=incident.id,
                role="assistant",
                content=f"echo: {message}",
                input_tokens=10,
                output_tokens=5,
            )
        )
        await self._uow.commit()
        return reply


def _fake_incident_chat(
    chat: ChatRepository = Depends(get_chat_repository),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> _FakeIncidentChat:
    return _FakeIncidentChat(chat, uow)


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
    app.dependency_overrides[get_incident_chat] = _fake_incident_chat
    app.dependency_overrides[get_current_user] = lambda: _ADMIN_USER
    # The endpoint 501s unless LLM_PROVIDER=claude_cli; force it regardless of the ambient
    # environment so this test exercises the chat business logic, not the provider gate.
    app.dependency_overrides[get_settings] = lambda: Settings(llm_provider="claude_cli")

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
