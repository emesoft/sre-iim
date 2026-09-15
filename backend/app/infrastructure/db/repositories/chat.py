"""SQLAlchemy repository for the incident chat transcript and Claude Code session continuity
(implements ChatRepository, domain/incidents/ports.py)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete
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

    async def clear(self, incident_id: uuid.UUID) -> None:
        """Drop the transcript and the provider session for one incident.

        Needed because a session outlives the code that started it. The chat system prompt — the
        part that names the investigation tools — is only sent on a session's first turn, so a
        conversation begun before a tool existed never learns about it and answers "I have no way
        to check" forever. Starting over is the only way back, and deleting is honest about what
        it does rather than leaving a dead transcript on screen.
        """
        await self._s.execute(
            sa_delete(ChatMessageRow).where(ChatMessageRow.incident_id == incident_id)
        )
        await self._s.execute(
            sa_delete(ChatSessionRow).where(ChatSessionRow.incident_id == incident_id)
        )
        await self._s.flush()

    async def add_message(self, message: ChatMessage) -> ChatMessage:
        row = ChatMessageRow(
            incident_id=message.incident_id,
            role=message.role,
            content=message.content,
            input_tokens=message.input_tokens,
            cached_input_tokens=message.cached_input_tokens,
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
            .order_by(ChatMessageRow.seq.asc())
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [chat_message_to_domain(row) for row in rows]
