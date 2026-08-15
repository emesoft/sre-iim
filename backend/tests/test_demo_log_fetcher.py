"""Unit tests for the demo log source — the `LogFetcher` adapter used when `DEMO_LOGS=true`.

No network, no AWS: these pin the properties the demo flow depends on (lines land inside the
requested window, the SRE's pasted error message actually filters, and repeating a search returns
the identical result so the analysis cache can hit).
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.infrastructure.config import Settings
from app.infrastructure.logs.demo_fetcher import DemoLogFetcher
from app.infrastructure.logs.factory import build_log_fetcher

START = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)


def _settings(**overrides) -> Settings:
    base = dict(aws_region="ap-southeast-1")
    base.update(overrides)
    return Settings(_env_file=None, **base)


async def _fetch(log_group: str = "/ecs/gcm-api", **kwargs):
    return await DemoLogFetcher().fetch_logs(log_group, START, END, **kwargs)


@pytest.mark.asyncio
async def test_returns_lines_inside_the_requested_window_newest_first():
    events = await _fetch()

    assert events, "demo source should always produce lines"
    assert all(START <= e.timestamp <= END for e in events)
    assert [e.timestamp for e in events] == sorted(
        (e.timestamp for e in events), reverse=True
    ), "CloudWatch returns newest first; the demo source must match"


@pytest.mark.asyncio
async def test_same_query_returns_identical_lines():
    """Deterministic output keeps the incident fingerprint stable, so re-running a search
    demonstrates a cache HIT instead of silently re-billing an LLM call."""
    first = await _fetch()
    second = await _fetch()

    assert [(e.timestamp, e.message) for e in first] == [
        (e.timestamp, e.message) for e in second
    ]


@pytest.mark.asyncio
async def test_scenario_follows_the_log_group_name():
    oom = await _fetch("/ecs/gcm-worker-oom")
    http = await _fetch("/aws/alb/gcm-public-5xx")

    assert any("OOMKilled" in e.message for e in oom)
    assert any("502" in e.message or "504" in e.message for e in http)


@pytest.mark.asyncio
async def test_unknown_log_group_still_yields_generic_errors():
    events = await _fetch("/ecs/some-unmapped-service")

    assert any("ERROR" in e.message for e in events)


@pytest.mark.asyncio
async def test_filter_pattern_keeps_only_matching_lines_case_insensitively():
    events = await _fetch("/ecs/gcm-worker-oom", filter_pattern="oomkilled")

    assert events
    assert all("oomkilled" in e.message.lower() for e in events)


@pytest.mark.asyncio
async def test_filter_pattern_with_regex_metacharacters_is_treated_literally():
    """An SRE pastes the raw error text from the alert; it may contain `(`, `[`, `*` — that must
    filter rather than blow up as an invalid regex."""
    events = await _fetch("/ecs/gcm-worker-oom", filter_pattern="Killed (exit 137")

    assert events, "the literal text appears in the OOM scenario, so it must match"
    assert all("killed (exit 137" in e.message.lower() for e in events)


@pytest.mark.asyncio
async def test_filter_pattern_matching_nothing_returns_empty():
    assert await _fetch(filter_pattern="zzz-no-such-error") == []


def test_factory_returns_the_demo_fetcher_for_any_service_when_demo_logs_is_on(monkeypatch):
    """DEMO_LOGS is a global switch: no `PROJECT_<SERVICE>_*` block needs to exist first."""
    monkeypatch.delenv("PROJECT_ANYTHING_CLOUD", raising=False)

    fetcher = build_log_fetcher("anything", _settings(demo_logs=True))

    assert isinstance(fetcher, DemoLogFetcher)


def test_demo_logs_wins_over_a_configured_project_cloud(monkeypatch):
    monkeypatch.setenv("PROJECT_OTHERTEAM_CLOUD", "azure")

    fetcher = build_log_fetcher("OtherTeam", _settings(demo_logs=True))

    assert isinstance(fetcher, DemoLogFetcher)
