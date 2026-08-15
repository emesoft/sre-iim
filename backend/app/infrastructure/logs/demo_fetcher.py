"""Demo log source — implements the domain `LogFetcher` port without touching any cloud.

Enabled globally with `DEMO_LOGS=true` so the log-search action (and everything downstream of it:
re-analysis, known-issue matching, ticketing, the daily report) can be exercised end-to-end with no
AWS account, no SSO profile, and no `PROJECT_<SERVICE>_*` block to declare first.

Two properties matter and are pinned by tests:

- **Deterministic.** The same query returns byte-identical lines, so the incident fingerprint is
  stable and re-running a search demonstrates a real cache HIT instead of re-billing the LLM.
- **Shaped like the real thing.** Newest first (the order Logs Insights returns), timestamps inside
  the requested window, and `filter_pattern` actually filters — the SRE pastes the error text from
  the alert and sees only the matching lines, exactly as against CloudWatch.

This is a development/demo adapter. It never runs when `DEMO_LOGS` is off.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.domain.incidents.entities import LogEvent

_LINE_COUNT = 20

_OOM_LINES = (
    "WARN  [ecs-agent] Task gcm-worker memory utilization 96% of 512 MiB limit",
    "WARN  [gcm-worker] GC pause 1.8s, heap 498 MiB / 512 MiB",
    "ERROR [gcm-worker] MemoryError: unable to allocate 128 MiB for a batch of 5000 records",
    'ERROR [gcm-worker] Traceback (most recent call last): File "/app/worker/batch.py", line 142, '
    "in process_batch",
    "FATAL [ecs-agent] Essential container in task exited: OOMKilled (exit 137)",
    "INFO  [ecs-agent] Stopping task gcm-worker/8f2ad3 - reason: OOMKilled (exit 137)",
    "ERROR [gcm-api] upstream gcm-worker unavailable, 12 jobs requeued",
)

_HTTP_5XX_LINES = (
    "WARN  [alb] Target 10.0.3.41:8080 failed health check (2/3) - connection refused",
    "ERROR [alb] HTTP 502 Bad Gateway target=10.0.3.41:8080 elapsed=30.01s",
    "ERROR [alb] HTTP 504 Gateway Timeout target=10.0.3.41:8080 elapsed=60.00s",
    "ERROR [gcm-api] upstream request timeout calling payments-svc after 30000ms",
    "ERROR [gcm-api] 500 Internal Server Error POST /api/v1/orders - connection pool exhausted",
    "WARN  [gcm-api] db connection pool at 20/20, 43 requests queued",
    "ERROR [gcm-api] Exception: TimeoutError raised while acquiring a pooled connection",
)

_GENERIC_LINES = (
    "ERROR [gcm-api] Unhandled exception in request handler: KeyError: 'customer_id'",
    'ERROR [gcm-api] Traceback (most recent call last): File "/app/api/orders.py", line 88, '
    "in create_order",
    "WARN  [gcm-api] retry 2/3 for downstream call to inventory-svc",
    "ERROR [gcm-api] psycopg.OperationalError: connection to server failed: timeout expired",
    "FATAL [gcm-api] worker 3 exited unexpectedly (signal 11)",
    "ERROR [gcm-api] 500 Internal Server Error GET /api/v1/orders/{id}",
)

# Matched in order against the lowercased log group name; the first hit wins.
_SCENARIOS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("oom", "mem", "worker"), _OOM_LINES),
    (("alb", "5xx", "api", "gateway"), _HTTP_5XX_LINES),
)


def _lines_for(log_group: str) -> tuple[str, ...]:
    name = log_group.lower()
    for keywords, lines in _SCENARIOS:
        if any(keyword in name for keyword in keywords):
            return lines
    return _GENERIC_LINES


def _matches(pattern: str, message: str) -> bool:
    """Logs Insights treats the filter as a regex, so honor that — but an SRE pastes raw error text
    from an alert, which may contain unbalanced `(` or a stray `*`. Fall back to a literal
    case-insensitive search rather than failing the search on an invalid pattern."""
    try:
        return re.search(pattern, message, re.IGNORECASE) is not None
    except re.error:
        return pattern.lower() in message.lower()


class DemoLogFetcher:
    """LogFetcher that synthesizes plausible incident log lines instead of querying a cloud."""

    def __init__(self, *, line_count: int = _LINE_COUNT) -> None:
        self._line_count = line_count

    async def fetch_logs(
        self,
        log_group: str,
        start: datetime,
        end: datetime,
        filter_pattern: str | None = None,
    ) -> list[LogEvent]:
        lines = _lines_for(log_group)
        # Spread the lines evenly across the window, endpoints excluded, so every timestamp is
        # strictly inside the range the caller asked about.
        step = (end - start) / (self._line_count + 1)
        events = [
            LogEvent(timestamp=start + step * (i + 1), message=lines[i % len(lines)])
            for i in range(self._line_count)
        ]
        if filter_pattern:
            events = [e for e in events if _matches(filter_pattern, e.message)]
        # Logs Insights sorts newest first; keep the same contract so callers see one shape.
        return list(reversed(events))
