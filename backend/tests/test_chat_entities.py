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
