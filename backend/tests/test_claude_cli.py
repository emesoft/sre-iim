"""Unit tests for ClaudeCliAnalyzer / ClaudeCliChatModel — no real subprocess, no DB.

Monkeypatches the module-level `_call_claude_cli` and `_get_token` helpers so these tests never
shell out to the real `claude` binary or hit Postgres.
"""

import uuid

import pytest

from app.infrastructure.config import Settings
from app.infrastructure.llm import claude_cli
from app.infrastructure.llm.claude_cli import (
    ClaudeCliAnalyzer,
    ClaudeCliChatModel,
    verify_claude_cli_token,
)
from app.infrastructure.llm.parsing import AnalysisError

pytestmark = pytest.mark.asyncio

_ANALYSIS_JSON = (
    '{"severity": "high", "summary": "s", "root_cause": "r", '
    '"recommended_action": "a", "confidence": "high"}'
)


def _settings(**overrides) -> Settings:
    return Settings(claude_cli_model="sonnet", **overrides)


async def test_analyzer_returns_a_draft_from_the_cli_response(monkeypatch):
    calls = []

    async def fake_call(*, prompt, system_prompt, model, token):
        calls.append({"prompt": prompt, "system_prompt": system_prompt, "model": model, "token": token})
        return claude_cli._CliResult(text=_ANALYSIS_JSON, input_tokens=120, output_tokens=45)

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_call_claude_cli", fake_call)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    analyzer = ClaudeCliAnalyzer(_settings())
    draft = await analyzer.analyze({"service": "GCM", "alert": "OOM"})

    assert draft.severity == "high"
    assert draft.model_id == "claude-cli:sonnet"
    assert draft.input_tokens == 120
    assert draft.output_tokens == 45
    assert len(calls) == 1
    assert calls[0]["model"] == "sonnet"
    assert calls[0]["token"] == "test-token"
    assert "GCM" in calls[0]["prompt"]


async def test_analyzer_raises_analysis_error_on_bad_json(monkeypatch):
    async def fake_call(**_kwargs):
        return claude_cli._CliResult(text="not json", input_tokens=None, output_tokens=None)

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_call_claude_cli", fake_call)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    analyzer = ClaudeCliAnalyzer(_settings())
    with pytest.raises(AnalysisError):
        await analyzer.analyze({"service": "GCM"})


async def test_analyzer_propagates_missing_token_error(monkeypatch):
    async def fake_get_token(settings):
        raise AnalysisError("Claude Code token is not configured")

    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    analyzer = ClaudeCliAnalyzer(_settings())
    with pytest.raises(AnalysisError, match="not configured"):
        await analyzer.analyze({"service": "GCM"})


async def test_chat_model_returns_the_cli_result_text(monkeypatch):
    async def fake_call(*, prompt, system_prompt, model, token):
        assert prompt == "user question"
        assert system_prompt == "be terse"
        return claude_cli._CliResult(text="42", input_tokens=10, output_tokens=2)

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_call_claude_cli", fake_call)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    chat = ClaudeCliChatModel(_settings())
    result = await chat.complete("be terse", "user question")
    assert result == "42"


async def test_verify_reports_ok_when_the_cli_call_succeeds(monkeypatch):
    async def fake_call(**_kwargs):
        return "OK"

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_call_claude_cli", fake_call)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    ok, error = await verify_claude_cli_token(_settings())
    assert ok is True
    assert error is None


async def test_verify_reports_the_cli_error_when_the_token_is_invalid(monkeypatch):
    async def fake_call(**_kwargs):
        raise RuntimeError("claude CLI failed: Failed to authenticate. API Error: 401 Invalid bearer token")

    async def fake_get_token(settings):
        return "stale-token"

    monkeypatch.setattr(claude_cli, "_call_claude_cli", fake_call)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    ok, error = await verify_claude_cli_token(_settings())
    assert ok is False
    assert "401" in error


async def test_call_claude_cli_parses_usage_from_the_subprocess_json(monkeypatch):
    payload = (
        '{"result": "hello", "is_error": false, '
        '"usage": {"input_tokens": 300, "output_tokens": 80}}'
    )

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return payload.encode(), b""

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await claude_cli._call_claude_cli(
        prompt="p", system_prompt=None, model="sonnet", token="t"
    )

    assert result.text == "hello"
    assert result.input_tokens == 300
    assert result.output_tokens == 80


async def test_call_claude_cli_includes_cache_tokens_in_input_tokens(monkeypatch):
    """--safe-mode still caches most of the prompt across calls in the same OAuth session — that
    shows up as cache_read/cache_creation, not input_tokens, but it's still real spend."""
    payload = (
        '{"result": "hello", "is_error": false, "usage": {'
        '"input_tokens": 2, "cache_read_input_tokens": 4461, '
        '"cache_creation_input_tokens": 100, "output_tokens": 13}}'
    )

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return payload.encode(), b""

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await claude_cli._call_claude_cli(
        prompt="p", system_prompt=None, model="sonnet", token="t"
    )

    assert result.input_tokens == 2 + 4461 + 100
    assert result.output_tokens == 13


async def test_call_claude_cli_tolerates_a_missing_usage_field(monkeypatch):
    payload = '{"result": "hello", "is_error": false}'

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return payload.encode(), b""

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await claude_cli._call_claude_cli(
        prompt="p", system_prompt=None, model="sonnet", token="t"
    )

    assert result.text == "hello"
    assert result.input_tokens is None
    assert result.output_tokens is None


async def test_verify_reports_when_no_token_is_configured(monkeypatch):
    async def fake_get_token(settings):
        raise AnalysisError("Claude Code token is not configured")

    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    ok, error = await verify_claude_cli_token(_settings())
    assert ok is False
    assert "not configured" in error


async def test_chat_sends_session_id_for_a_new_session(monkeypatch):
    calls = []

    async def fake_run_cli(cmd, token):
        calls.append(cmd)
        return claude_cli._CliResult(text="answer", input_tokens=50, output_tokens=20)

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_run_cli", fake_run_cli)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    chat = claude_cli.ClaudeCliChat(_settings())
    session_id = uuid.uuid4()
    result = await chat.send(
        service="GCM", context={"service": "GCM"}, message="check logs",
        claude_session_id=session_id, is_new_session=True,
    )

    assert result.text == "answer"
    assert result.claude_session_id == session_id
    assert len(calls) == 1
    assert "--session-id" in calls[0]
    assert "--resume" not in calls[0]
    assert "--mcp-config" in calls[0]
    assert "--strict-mcp-config" in calls[0]
    assert "mcp__iim-tools__fetch_logs" in calls[0]


async def test_chat_resumes_an_existing_session(monkeypatch):
    calls = []

    async def fake_run_cli(cmd, token):
        calls.append(cmd)
        return claude_cli._CliResult(text="follow-up answer", input_tokens=5, output_tokens=3)

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_run_cli", fake_run_cli)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    chat = claude_cli.ClaudeCliChat(_settings())
    session_id = uuid.uuid4()
    result = await chat.send(
        service="GCM", context={}, message="and then?",
        claude_session_id=session_id, is_new_session=False,
    )

    assert result.claude_session_id == session_id
    assert "--resume" in calls[0]
    assert "--session-id" not in calls[0]


async def test_chat_falls_back_to_a_fresh_session_when_resume_fails(monkeypatch):
    calls = []

    async def fake_run_cli(cmd, token):
        calls.append(cmd)
        if "--resume" in cmd:
            raise RuntimeError("claude CLI failed: No conversation found with session ID")
        return claude_cli._CliResult(text="fresh answer", input_tokens=1, output_tokens=1)

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_run_cli", fake_run_cli)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    chat = claude_cli.ClaudeCliChat(_settings())
    stale_session_id = uuid.uuid4()
    result = await chat.send(
        service="GCM", context={}, message="hi again",
        claude_session_id=stale_session_id, is_new_session=False,
    )

    assert result.text == "fresh answer"
    assert result.claude_session_id != stale_session_id
    assert len(calls) == 2
    assert "--resume" in calls[0]
    assert "--session-id" in calls[1]


async def test_chat_raises_when_a_brand_new_session_fails_outright(monkeypatch):
    async def fake_run_cli(cmd, token):
        raise RuntimeError("claude CLI failed: 401 Invalid bearer token")

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_run_cli", fake_run_cli)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    chat = claude_cli.ClaudeCliChat(_settings())
    with pytest.raises(RuntimeError, match="401"):
        await chat.send(
            service="GCM", context={}, message="hi",
            claude_session_id=uuid.uuid4(), is_new_session=True,
        )
