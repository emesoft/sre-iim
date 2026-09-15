"""Unit tests for the fetch_logs MCP tool function — calls it directly (no MCP transport, no
subprocess), monkeypatching resolve_log_fetcher so no real AWS/demo call happens.

Note: the installed `mcp` package (1.29.0, pinned <2 — see pyproject.toml) has `@mcp.tool()`
return the original async function unchanged rather than wrapping it in a `Tool` object, so
`fetch_logs` itself is directly callable/awaitable — there is no `.fn` attribute to unwrap.
"""

import os

os.environ.setdefault("IIM_INCIDENT_SERVICE", "GCM")  # read at module import time

from datetime import datetime

import pytest

from app.domain.incidents.entities import LogEvent
from app.infrastructure.llm import mcp_log_tool

pytestmark = pytest.mark.asyncio


def _fake_factory(fetcher):
    """`resolve_log_fetcher` is async now — it looks the project's integration up in the DB."""

    async def _factory(service, settings):
        return fetcher

    return _factory


class _FakeFetcher:
    def __init__(self, events=None, raises=None):
        self._events = events or []
        self._raises = raises

    async def fetch_logs(self, log_group, start, end, filter_pattern=None):
        if self._raises:
            raise self._raises
        return self._events


async def test_fetch_logs_returns_formatted_lines(monkeypatch):
    events = [LogEvent(timestamp=datetime(2026, 8, 23, 10, 0), message="boom", level="ERROR")]
    monkeypatch.setattr(
        mcp_log_tool, "resolve_log_fetcher", _fake_factory(_FakeFetcher(events=events))
    )

    result = await mcp_log_tool.fetch_logs(
        log_group="/ecs/prod", start="2026-08-23T09:00:00", end="2026-08-23T11:00:00"
    )

    assert "boom" in result
    assert "ERROR" in result


async def test_fetch_logs_reports_no_matches(monkeypatch):
    monkeypatch.setattr(
        mcp_log_tool, "resolve_log_fetcher", _fake_factory(_FakeFetcher(events=[]))
    )

    result = await mcp_log_tool.fetch_logs(
        log_group="/ecs/prod", start="2026-08-23T09:00:00", end="2026-08-23T11:00:00"
    )

    assert result == "(no matching log lines in this window)"


async def test_fetch_logs_reports_fetcher_errors_as_text_not_a_crash(monkeypatch):
    monkeypatch.setattr(
        mcp_log_tool,
        "resolve_log_fetcher",
        _fake_factory(_FakeFetcher(raises=RuntimeError("SSO token expired"))),
    )

    result = await mcp_log_tool.fetch_logs(
        log_group="/ecs/prod", start="2026-08-23T09:00:00", end="2026-08-23T11:00:00"
    )

    assert "fetch_logs failed" in result
    assert "SSO token expired" in result
