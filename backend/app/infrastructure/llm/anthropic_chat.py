"""Incident chat over the Anthropic Messages API, with our own tool loop.

Chat ran only through the Claude Code CLI, and measurement showed why that was the wrong backend
for it: a bare `claude -p "say ok"` — no MCP config, no system prompt, no context — costs ~29,000
tokens, because the CLI carries its own coding-agent system prompt and built-in tool definitions on
every call. Against that, everything this feature actually needs (incident context, chat system
prompt, four tool schemas) came to ~900. Roughly 97% of every chat turn was harness, re-paid on
each internal step of a tool-using turn — which is how single turns reached 95k and 135k.

So this speaks to the API directly and runs the tool loop itself: request → `tool_use` → run the
tool → `tool_result` → repeat. Same four tools, same project scoping, a fraction of the tokens, and
no subprocess per turn.

Conversation state lives in our own `chat_messages` rather than in a provider-side session, so the
transcript we show is the transcript the model saw.

Messages API tool use: https://docs.anthropic.com/en/docs/build-with-claude/tool-use
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.domain.incidents.entities import ChatMessage, ChatTurnResult
from app.infrastructure.config import Settings
from app.infrastructure.llm.incident_tools import TOOL_SCHEMAS, IncidentTools

logger = logging.getLogger(__name__)

_MAX_TOKENS = 1500
#: A turn may call tools several times before answering; this stops a confused loop from spending
#: the conversation's budget. Reached, the model is asked to answer with what it has.
_MAX_TOOL_ROUNDS = 6

_SYSTEM = """You are a senior SRE helping investigate one specific incident in a chat conversation. \
You have the incident's full raw context below. Answer grounded in that context.

You can investigate this incident's own AWS account directly through the tools provided. Use them \
rather than telling the user to go and run commands themselves — if a question can be answered by a \
tool, answer it. Never ask the user to run an AWS command you could run yourself, and never ask for \
AWS credentials; you already act as this project's own integration. If a tool reports no integration \
is configured, say so plainly.

Never invent log content, metrics, or events that aren't in the context or in a tool result. Be \
concise and direct, like an engineer working the incident live."""


@dataclass
class AnthropicIncidentChat:
    """IncidentChatProvider backed by the Messages API."""

    api_key: str
    model: str
    settings: Settings

    async def send(
        self,
        *,
        service: str,
        context: dict,
        message: str,
        claude_session_id: uuid.UUID,
        is_new_session: bool,
        history: list[ChatMessage] | None = None,
    ) -> ChatTurnResult:
        if not self.api_key:
            raise RuntimeError("No Anthropic API key is configured for the active model profile")

        client = AsyncAnthropic(api_key=self.api_key)
        tools = IncidentTools(service=service, settings=self.settings)
        system = (
            f"{_SYSTEM}\n\nIncident context:\n{json.dumps(context, indent=2, default=str)}"
        )
        messages = [
            {"role": m.role, "content": m.content} for m in (history or []) if m.content
        ]
        messages.append({"role": "user", "content": message})

        fresh = cached = output = 0
        text = ""
        for _round in range(_MAX_TOOL_ROUNDS):
            reply = await client.messages.create(
                model=self.model,
                max_tokens=_MAX_TOKENS,
                system=system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
            usage = reply.usage
            fresh += usage.input_tokens or 0
            cached += (getattr(usage, "cache_read_input_tokens", 0) or 0) + (
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            )
            output += usage.output_tokens or 0
            text = "".join(b.text for b in reply.content if b.type == "text").strip()

            calls = [b for b in reply.content if b.type == "tool_use"]
            if not calls:
                break

            # The assistant turn has to be replayed verbatim — including the tool_use blocks — or
            # the tool results have nothing to attach to.
            messages.append({"role": "assistant", "content": reply.content})
            results = []
            for call in calls:
                logger.info("incident chat tool %s(%s)", call.name, call.input)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": await tools.run(call.name, dict(call.input or {})),
                    }
                )
            messages.append({"role": "user", "content": results})
        else:
            # Out of rounds with tools still pending: say so rather than returning whatever partial
            # text happens to be in hand, which would read as a confident answer.
            text = text or (
                "I ran out of investigation steps before reaching an answer. Ask again with a "
                "narrower question and I'll go straight at it."
            )

        return ChatTurnResult(
            text=text,
            input_tokens=fresh,
            cached_input_tokens=cached,
            output_tokens=output,
            # No provider-side session to track: the transcript is ours. Echoed back unchanged so
            # the use case's "did the provider start a new session" check stays a no-op here.
            claude_session_id=claude_session_id,
        )
