"""Unit tests for incident chat over the Messages API — no network, no AWS.

This backend exists because measurement showed a bare `claude -p "say ok"` costs ~29,000 tokens of
the CLI's own coding-agent harness, against ~900 for everything this feature actually needs. The
saving is only real if the tool loop here is correct, so that is what these cover: the loop's shape,
its termination, and the two ways it can silently lie — dropping the transcript, or answering
confidently after running out of steps.
"""

import uuid
from dataclasses import dataclass

import pytest

from app.domain.incidents.entities import ChatMessage
from app.infrastructure.config import Settings
from app.infrastructure.llm import anthropic_chat
from app.infrastructure.llm.anthropic_chat import AnthropicIncidentChat


@dataclass
class _Text:
    text: str
    type: str = "text"


@dataclass
class _ToolUse:
    name: str
    input: dict
    id: str = "call-1"
    type: str = "tool_use"


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 20
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Reply:
    content: list
    usage: _Usage


class _FakeMessages:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._replies.pop(0)


class _FakeClient:
    def __init__(self, replies):
        self.messages = _FakeMessages(replies)


def _provider(monkeypatch, replies, tool_result="{}"):
    client = _FakeClient(replies)
    monkeypatch.setattr(anthropic_chat, "AsyncAnthropic", lambda api_key: client)

    class FakeTools:
        def __init__(self, **_kw):
            self.ran: list[tuple] = []

        async def run(self, name, args):
            self.ran.append((name, args))
            return tool_result

    tools = FakeTools()
    monkeypatch.setattr(anthropic_chat, "IncidentTools", lambda **_kw: tools)
    provider = AnthropicIncidentChat(
        api_key="sk-ant-test", model="claude-opus-5",
        settings=Settings(secret_encryption_key="x" * 44),
    )
    return provider, client, tools


async def _send(provider, history=None, message="why did this fire?"):
    return await provider.send(
        service="EVP", context={"service": "EVP"}, message=message,
        claude_session_id=uuid.uuid4(), is_new_session=not history, history=history,
    )


async def test_a_plain_answer_takes_one_call(monkeypatch):
    provider, client, tools = _provider(
        monkeypatch, [_Reply([_Text("It's a scale-in alarm.")], _Usage())]
    )
    result = await _send(provider)
    assert result.text == "It's a scale-in alarm."
    assert len(client.messages.calls) == 1
    assert tools.ran == []


async def test_a_tool_call_is_run_and_fed_back(monkeypatch):
    """The loop's whole purpose: the model asks, we answer from the real account, it concludes."""
    provider, client, tools = _provider(
        monkeypatch,
        [
            _Reply([_ToolUse("ecs_service_state", {"cluster": "c", "service": "s"})], _Usage()),
            _Reply([_Text("running=0 of 1 desired — the service is scaled to zero.")], _Usage()),
        ],
        tool_result='{"desired": 1, "running": 0}',
    )
    result = await _send(provider)

    assert tools.ran == [("ecs_service_state", {"cluster": "c", "service": "s"})]
    assert "scaled to zero" in result.text
    # The second request must carry the assistant's tool_use turn *and* our tool_result, or the
    # result has nothing to attach to and the API rejects it.
    second = client.messages.calls[1]["messages"]
    assert second[-2]["role"] == "assistant"
    assert second[-1]["content"][0]["type"] == "tool_result"
    assert second[-1]["content"][0]["tool_use_id"] == "call-1"


async def test_tokens_are_summed_across_the_whole_turn(monkeypatch):
    """A turn is several API calls; reporting only the last would under-count what it cost."""
    provider, _, _ = _provider(
        monkeypatch,
        [
            _Reply([_ToolUse("describe_alarm", {"alarm_name": "a"})], _Usage(input_tokens=100)),
            _Reply([_Text("done")], _Usage(input_tokens=250, output_tokens=30)),
        ],
    )
    result = await _send(provider)
    assert result.input_tokens == 350
    assert result.output_tokens == 50


async def test_cache_tokens_stay_separate(monkeypatch):
    provider, _, _ = _provider(
        monkeypatch,
        [_Reply([_Text("hi")], _Usage(input_tokens=12, cache_read_input_tokens=900))],
    )
    result = await _send(provider)
    assert (result.input_tokens, result.cached_input_tokens) == (12, 900)


async def test_the_stored_transcript_is_replayed(monkeypatch):
    """There is no provider-side session here. Drop the history and every turn starts amnesiac
    while the UI shows a conversation — the model and the reader seeing different things."""
    provider, client, _ = _provider(monkeypatch, [_Reply([_Text("ok")], _Usage())])
    history = [
        ChatMessage(incident_id=uuid.uuid4(), role="user", content="what fired?"),
        ChatMessage(incident_id=uuid.uuid4(), role="assistant", content="a CPU alarm"),
    ]
    await _send(provider, history=history)
    sent = client.messages.calls[0]["messages"]
    assert [m["content"] for m in sent] == ["what fired?", "a CPU alarm", "why did this fire?"]


async def test_a_runaway_tool_loop_stops_and_says_so(monkeypatch):
    """Left unbounded a confused model can call tools until the budget is gone. Stopping silently
    would be worse than stopping loudly: whatever half-thought is in hand would read as the
    answer."""
    looping = [
        _Reply([_ToolUse("describe_alarm", {"alarm_name": "a"})], _Usage()) for _ in range(20)
    ]
    provider, client, _ = _provider(monkeypatch, looping)
    result = await _send(provider)
    assert len(client.messages.calls) == anthropic_chat._MAX_TOOL_ROUNDS
    assert "ran out of investigation steps" in result.text


async def test_no_api_key_fails_loudly(monkeypatch):
    """Silently falling back to the CLI would hide a misconfigured profile behind a 29k-token bill
    the operator thought they had moved off."""
    provider = AnthropicIncidentChat(
        api_key="", model="claude-opus-5", settings=Settings(secret_encryption_key="x" * 44)
    )
    with pytest.raises(RuntimeError, match="No Anthropic API key"):
        await _send(provider)
