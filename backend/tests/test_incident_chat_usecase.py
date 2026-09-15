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
        self.sessions[incident_id] = ChatSession(
            incident_id=incident_id, claude_session_id=claude_session_id
        )

    async def add_message(self, message):
        stored = ChatMessage(
            incident_id=message.incident_id, role=message.role, content=message.content,
            input_tokens=message.input_tokens,
            cached_input_tokens=message.cached_input_tokens,
            output_tokens=message.output_tokens,
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

    async def send(
        self, *, service, context, message, claude_session_id, is_new_session, history=None
    ):
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
            text="here's what I see", input_tokens=100, cached_input_tokens=9000, output_tokens=40,
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
            text="second reply", input_tokens=10, cached_input_tokens=20, output_tokens=5,
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
            text="restarted", input_tokens=1, cached_input_tokens=0, output_tokens=1,
            claude_session_id=fresh_session_id,
        )
    )
    use_case = IncidentChat(
        incidents=FakeIncidentRepo(), chat=chat_repo, claude_chat=provider, uow=FakeUnitOfWork()
    )

    await use_case.send_message(incident, "hi")

    updated = await chat_repo.get_or_create_session(incident.id)
    assert updated.claude_session_id == fresh_session_id
    assert updated.claude_session_id != original_session.claude_session_id
