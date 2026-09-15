"""Generic LLM chat ports (decision 0011 for ChatModel; design spec 2026-08-23 for
IncidentChatProvider).

`ChatModel` is used by the multi-agent graph nodes and the daily report — free-form completions,
no tools. `IncidentChatProvider` is used by the incident chat feature — one turn, with real
tool-calling (the provider's own agentic loop decides whether to call a tool). Only `claude_cli`
implements `IncidentChatProvider`; other providers are rejected with a 501 at the HTTP layer
before this port is ever called (see `interface/http/incidents.py`).
"""

from __future__ import annotations

import uuid
from typing import Literal, Protocol

from app.domain.incidents.entities import ChatMessage, ChatTurnResult

Tier = Literal["fast", "main"]


class ChatModel(Protocol):
    async def complete(self, system: str, user: str, *, tier: Tier = "main") -> str: ...


class IncidentChatProvider(Protocol):
    """One incident-chat turn, with real tool-calling — see module docstring."""

    async def send(
        self,
        *,
        service: str,
        context: dict,
        message: str,
        claude_session_id: uuid.UUID,
        is_new_session: bool,
        history: list["ChatMessage"] | None = None,
    ) -> ChatTurnResult:
        """`history` is the stored transcript. A provider that keeps its own conversation state
        server-side (the Claude Code CLI, via `--resume`) ignores it; one talking to a stateless
        API needs it, and taking it from our own table means the transcript shown is the transcript
        the model saw."""
        ...
