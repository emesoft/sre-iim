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
            history=existing,
        )
        if result.claude_session_id != session.claude_session_id:
            await self.chat.replace_session_id(incident.id, result.claude_session_id)

        reply = await self.chat.add_message(
            ChatMessage(
                incident_id=incident.id,
                role="assistant",
                content=result.text,
                input_tokens=result.input_tokens,
                cached_input_tokens=result.cached_input_tokens,
                output_tokens=result.output_tokens,
            )
        )
        await self.uow.commit()
        return reply
