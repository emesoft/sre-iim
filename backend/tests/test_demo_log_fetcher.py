"""Unit tests for the demo log source — the `LogFetcher` adapter used when `DEMO_LOGS=true`.

No network, no AWS: these pin the properties the demo flow depends on (lines land inside the
requested window, the SRE's pasted error message actually filters, every line carries a level the
prompt can render, and repeating a search returns the identical result so the analysis cache hits).
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.infrastructure.config import Settings
from app.infrastructure.logs.demo_fetcher import DemoLogFetcher
from app.infrastructure.logs.factory import build_log_fetcher

START = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)
LEVELS = {"DEBUG", "INFO", "WARN", "ERROR", "FATAL"}


def _settings(**overrides) -> Settings:
    base = dict(aws_region="ap-southeast-1")
    base.update(overrides)
    return Settings(_env_file=None, **base)


async def _fetch(log_group: str = "/ecs/gcm-service", **kwargs):
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

    assert [(e.timestamp, e.level, e.message) for e in first] == [
        (e.timestamp, e.level, e.message) for e in second
    ]


@pytest.mark.asyncio
async def test_every_line_carries_a_severity_level():
    """The prompt renders `{ts} {level} {message}` per line — a missing level shows up as `None`
    in the text the model reads."""
    events = await _fetch()

    assert all(e.level in LEVELS for e in events)


@pytest.mark.asyncio
async def test_every_line_carries_a_component_and_structured_context():
    """Realistic shape: `component  key=value ...` then the human-readable detail."""
    events = await _fetch()

    for e in events:
        head = e.message.splitlines()[0]
        assert "=" in head, f"expected key=value context in {head!r}"


@pytest.mark.parametrize(
    ("log_group", "expected"),
    [
        ("/ecs/gcm-worker-oom", "OOMKilled"),
        ("/aws/alb/gcm-public-5xx", "502"),
        ("/ecs/gcm-postgres-db", "connection pool"),
        ("/ecs/gcm-disk-volume", "No space left on device"),
        ("/ecs/gcm-tls-cert", "certificate"),
        ("/ecs/gcm-kafka-queue", "consumer lag"),
        ("/ecs/gcm-vendor-quota", "429"),
    ],
)
@pytest.mark.asyncio
async def test_scenario_follows_the_log_group_name(log_group: str, expected: str):
    events = await _fetch(log_group)

    blob = "\n".join(e.message for e in events)
    assert expected.lower() in blob.lower(), f"{log_group} should produce {expected!r} lines"


@pytest.mark.asyncio
async def test_unknown_log_group_falls_back_to_generic_application_errors():
    events = await _fetch("/ecs/some-unmapped-service")

    assert any(e.level in ("ERROR", "FATAL") for e in events)


@pytest.mark.asyncio
async def test_filter_pattern_keeps_only_matching_lines_case_insensitively():
    events = await _fetch("/ecs/gcm-worker-oom", filter_pattern="oomkilled")

    assert events
    assert all("oomkilled" in e.message.lower() for e in events)


@pytest.mark.asyncio
async def test_filter_pattern_with_regex_metacharacters_is_treated_literally():
    """An SRE pastes the raw error text from the alert; it may contain `(`, `[`, `*` — that must
    filter rather than blow up as an invalid regex."""
    events = await _fetch("/ecs/gcm-worker-oom", filter_pattern="OOMKilled (exit 137")

    assert events, "the literal text appears in the OOM scenario, so it must match"
    assert all("oomkilled (exit 137" in e.message.lower() for e in events)


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
