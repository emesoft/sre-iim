"""Unit tests for ClaudeCliAnalyzer / ClaudeCliChatModel — no real subprocess, no DB.

Monkeypatches the module-level `_call_claude_cli` and `_get_token` helpers so these tests never
shell out to the real `claude` binary or hit Postgres.
"""

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
        return _ANALYSIS_JSON

    async def fake_get_token(settings):
        return "test-token"

    monkeypatch.setattr(claude_cli, "_call_claude_cli", fake_call)
    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    analyzer = ClaudeCliAnalyzer(_settings())
    draft = await analyzer.analyze({"service": "GCM", "alert": "OOM"})

    assert draft.severity == "high"
    assert draft.model_id == "claude-cli:sonnet"
    assert len(calls) == 1
    assert calls[0]["model"] == "sonnet"
    assert calls[0]["token"] == "test-token"
    assert "GCM" in calls[0]["prompt"]


async def test_analyzer_raises_analysis_error_on_bad_json(monkeypatch):
    async def fake_call(**_kwargs):
        return "not json"

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
        return "42"

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


async def test_verify_reports_when_no_token_is_configured(monkeypatch):
    async def fake_get_token(settings):
        raise AnalysisError("Claude Code token is not configured")

    monkeypatch.setattr(claude_cli, "_get_token", fake_get_token)

    ok, error = await verify_claude_cli_token(_settings())
    assert ok is False
    assert "not configured" in error
