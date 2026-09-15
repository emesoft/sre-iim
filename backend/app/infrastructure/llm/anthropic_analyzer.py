"""Anthropic API adapter — implements the domain `Analyzer` and `ChatModel` ports.

The other way to reach Claude. `claude_cli` spends a Claude Code *subscription* through the local
CLI (local demo only, no key), Bedrock goes through an AWS account; this one is a plain API key
billed per token, which is what a deployed instance normally wants.

Uses the official SDK rather than raw httpx — unlike the OpenAI-compatible adapter next door, this
is Anthropic's own wire format and the SDK is the documented way to speak it.

Messages API: https://docs.anthropic.com/en/api/messages
"""

from __future__ import annotations

from anthropic import AsyncAnthropic

from app.domain.documents.entities import RetrievedChunk
from app.domain.incidents.entities import AnalysisDraft
from app.domain.incidents.prompts import (
    RETRIEVED_KNOWLEDGE_RULES,
    SYSTEM_PROMPT,
    build_user_message,
)
from app.domain.llm import Tier
from app.infrastructure.llm.parsing import AnalysisError, parse_analysis

#: Analyses are a few hundred tokens of strict JSON; the graph's steps run slightly longer.
_ANALYSIS_MAX_TOKENS = 600
_CHAT_MAX_TOKENS = 700


def _text(message) -> str:
    """Concatenate the text blocks of a response, ignoring any non-text block."""
    return "".join(block.text for block in message.content if block.type == "text")


class AnthropicAnalyzer:
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    async def analyze(
        self, context: dict, evidence: list[RetrievedChunk] | None = None
    ) -> AnalysisDraft:
        if not self._api_key:
            raise AnalysisError("No Anthropic API key is configured")
        client = AsyncAnthropic(api_key=self._api_key)
        message = await client.messages.create(
            model=self._model,
            max_tokens=_ANALYSIS_MAX_TOKENS,
            temperature=0.2,
            system=SYSTEM_PROMPT + (RETRIEVED_KNOWLEDGE_RULES if evidence else ""),
            messages=[{"role": "user", "content": build_user_message(context, evidence)}],
        )
        return AnalysisDraft(model_id=self._model, **parse_analysis(_text(message)))


class AnthropicChatModel:
    """Graph-mode adapter. Tiering is real here: the cheap model runs the mechanical steps."""

    def __init__(self, api_key: str, model: str, fast_model: str | None = None) -> None:
        self._api_key = api_key
        self._model = model
        self._fast_model = fast_model or model

    async def complete(self, system: str, user: str, *, tier: Tier = "main") -> str:
        if not self._api_key:
            raise RuntimeError("No Anthropic API key is configured")
        client = AsyncAnthropic(api_key=self._api_key)
        message = await client.messages.create(
            model=self._fast_model if tier == "fast" else self._model,
            max_tokens=_CHAT_MAX_TOKENS,
            temperature=0.2,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return _text(message)
