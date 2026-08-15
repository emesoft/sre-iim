"""Demo log source — implements the domain `LogFetcher` port without touching any cloud.

Enabled globally with `DEMO_LOGS=true` so the log-search action (and everything downstream of it:
re-analysis, known-issue matching, ticketing and the daily report) can be exercised end-to-end with
no AWS account, no SSO profile, and no `PROJECT_<SERVICE>_*` block to declare first.

Three properties matter and are pinned by tests:

- **Deterministic.** The same query returns byte-identical lines, so the incident fingerprint is
  stable and re-running a search demonstrates a real cache HIT instead of re-billing the LLM.
- **Shaped like the real thing.** Newest first (the order Logs Insights returns), timestamps inside
  the requested window, a severity level on every line, and `filter_pattern` actually filters — the
  SRE pastes the error text from the alert and sees only the matching lines.
- **Carries enough signal to diagnose.** Each line is `component  key=value ...` followed by the
  detail, so the model reads concrete numbers (pool sizes, exit codes, lag) rather than prose.

The scenario is chosen from the log group name (see `_SCENARIOS`), which is how one demo covers
several incident types without any extra configuration.

This is a development/demo adapter. It never runs when `DEMO_LOGS` is off.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.domain.incidents.entities import LogEvent

_LINE_COUNT = 20

# Each entry: (level, component, "key=value ...", detail). Rendered as
#   component  key=value ...
#          detail
Line = tuple[str, str, str, str]

_OOM: tuple[Line, ...] = (
    ("WARN", "ecs-agent", "task=payment-service/8f2ad3 memory=486MiB/512MiB", "Task memory utilization 95% of limit"),
    ("WARN", "payment-svc", "heap=498MiB/512MiB gc_pause=1.8s", "Full GC did not reclaim memory; allocation stalling"),
    ("ERROR", "payment-svc", "batch_size=5000 requested=128MiB", "MemoryError: unable to allocate buffer for batch"),
    ("ERROR", "payment-svc", 'file=/app/worker/batch.py line=142', "Traceback (most recent call last):\n         File \"/app/worker/batch.py\", line 142, in process_batch\n           rows = [Record(**r) for r in payload]\n         MemoryError"),
    ("FATAL", "ecs-agent", "task=payment-service/8f2ad3 exit_code=137", "Essential container in task exited: OOMKilled (exit 137)"),
    ("INFO", "ecs-agent", "task=payment-service/8f2ad3 restarts_5m=11", "Stopping task - reason: OOMKilled (exit 137)"),
    ("ERROR", "payment-api", "upstream=payment-worker requeued=12", "Upstream worker unavailable, jobs requeued"),
)

_HTTP_5XX: tuple[Line, ...] = (
    ("WARN", "alb", "target=10.0.3.41:8080 checks=2/3", "Target failed health check - connection refused"),
    ("ERROR", "alb", "target=10.0.3.41:8080 status=502 elapsed=30.01s", "HTTP 502 Bad Gateway returned to client"),
    ("ERROR", "alb", "target=10.0.3.41:8080 status=504 elapsed=60.00s", "HTTP 504 Gateway Timeout - target did not respond"),
    ("ERROR", "gcm-api", "upstream=payments-svc timeout_ms=30000", "Upstream request timed out"),
    ("ERROR", "gcm-api", "route=POST /api/v1/orders status=500 pool=20/20", "Internal Server Error - connection pool exhausted"),
    ("WARN", "gcm-api", "pool=20/20 queued=43", "All pooled connections busy, requests queueing"),
)

_DATABASE: tuple[Line, ...] = (
    ("WARN", "pgbouncer", "pool=100/100 waiting=64", "Client connection pool saturated"),
    ("ERROR", "gcm-api", "pool=100/100 wait_ms=5000", "psycopg.OperationalError: connection pool exhausted, timeout expired"),
    ("ERROR", "postgres", "pid=48213 blocked_by=48119 relation=orders", "deadlock detected; transaction rolled back"),
    ("WARN", "postgres", "query_ms=8420 rows=1 relation=orders", "Slow query exceeded statement timeout warning threshold"),
    ("WARN", "postgres", "replica=replica-1 lag_bytes=734003200", "Streaming replica lag above threshold"),
    ("ERROR", "gcm-api", "route=GET /api/v1/orders status=500", "Request failed: could not acquire a database connection"),
)

_DISK: tuple[Line, ...] = (
    ("WARN", "node-agent", "mount=/var/lib/docker used=91% avail=4.1GiB", "Filesystem usage above warning threshold"),
    ("ERROR", "node-agent", "mount=/var/lib/docker used=100% avail=0B", "No space left on device"),
    ("ERROR", "gcm-api", "path=/var/log/app.log errno=28", "Failed to write log file: No space left on device"),
    ("WARN", "logrotate", "path=/var/log/app.log retained=0", "Log rotation failed, old segments not reclaimed"),
    ("FATAL", "postgres", "mount=/var/lib/postgresql avail=0B", "PANIC: could not write to WAL file: No space left on device"),
)

_CERT: tuple[Line, ...] = (
    ("WARN", "gcm-api", "host=payments.vendor.example expires_in=0d", "TLS certificate expires today"),
    ("ERROR", "gcm-api", "host=payments.vendor.example not_after=2026-08-15T00:00:00Z", "x509: certificate has expired or is not yet valid"),
    ("ERROR", "gcm-api", "host=payments.vendor.example alert=48", "TLS handshake failure: bad certificate"),
    ("ERROR", "gcm-api", "upstream=payments-svc attempts=3", "All retries failed: certificate verification error"),
    ("WARN", "alb", "listener=443 status=495", "Client requests rejected during TLS negotiation"),
)

_QUEUE: tuple[Line, ...] = (
    ("WARN", "kafka-consumer", "group=orders-worker lag=184320 partitions=6", "Consumer lag above threshold"),
    ("ERROR", "kafka-consumer", "group=orders-worker rebalance=4 in=5m", "Repeated consumer group rebalances, progress stalled"),
    ("WARN", "orders-worker", "processed_per_min=120 arriving_per_min=940", "Consumer lag growing: intake exceeds processing rate"),
    ("ERROR", "orders-worker", "dlq=orders-dlq depth=2417 delta_1h=+2110", "Dead-letter queue growing rapidly"),
    ("ERROR", "orders-worker", "msg_id=8fd21c retries=5", "Message exceeded retry budget, routed to dead-letter queue"),
)

_QUOTA: tuple[Line, ...] = (
    ("WARN", "search-gateway", "vendor=places-api calls_today=9120 budget=10000", "Third-party call volume approaching daily budget"),
    ("ERROR", "search-gateway", "vendor=places-api status=429 retry_after=60", "HTTP 429 Too Many Requests from vendor"),
    ("ERROR", "search-gateway", "vendor=places-api calls_today=11800 overage=1800", "Daily quota exceeded, calls now billable at $0.05 each"),
    ("WARN", "search-gateway", "cache_hit_rate=31% baseline=88%", "Cache hit rate collapsed, upstream call volume multiplied"),
    ("ERROR", "search-gateway", "route=GET /api/v1/search status=503", "Serving degraded results: vendor quota exhausted"),
)

_GENERIC: tuple[Line, ...] = (
    ("ERROR", "gcm-api", "route=POST /api/v1/orders field=customer_id", "Unhandled exception in request handler: KeyError: 'customer_id'"),
    ("ERROR", "gcm-api", "file=/app/api/orders.py line=88", "Traceback (most recent call last):\n         File \"/app/api/orders.py\", line 88, in create_order\n           customer = payload[\"customer_id\"]\n         KeyError: 'customer_id'"),
    ("WARN", "gcm-api", "downstream=inventory-svc attempt=2/3", "Retrying downstream call after timeout"),
    ("ERROR", "gcm-api", "host=db-primary timeout_ms=5000", "psycopg.OperationalError: connection to server failed: timeout expired"),
    ("FATAL", "gcm-api", "worker=3 signal=11", "Worker exited unexpectedly"),
)

# Matched in order against the lowercased log group name; the first hit wins. Keywords are kept
# mutually exclusive on purpose — an ambiguous one like "worker" or "api" would silently decide the
# scenario for a log group the SRE named for a different reason.
_SCENARIOS: tuple[tuple[tuple[str, ...], tuple[Line, ...]], ...] = (
    (("oom", "mem"), _OOM),
    (("alb", "5xx", "gateway", "http"), _HTTP_5XX),
    (("db", "postgres", "sql", "rds"), _DATABASE),
    (("disk", "volume", "storage"), _DISK),
    (("cert", "tls", "ssl"), _CERT),
    (("queue", "kafka", "sqs", "backlog"), _QUEUE),
    (("quota", "ratelimit", "vendor", "cost"), _QUOTA),
)


def _lines_for(log_group: str) -> tuple[Line, ...]:
    name = log_group.lower()
    for keywords, lines in _SCENARIOS:
        if any(keyword in name for keyword in keywords):
            return lines
    return _GENERIC


def _render(component: str, context: str, detail: str) -> str:
    """`component  key=value ...` then the detail, indented under it — the shape a structured
    logger emits, so the model reads concrete values rather than a prose sentence."""
    return f"{component}  {context}\n       {detail}"


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
        events = []
        for i in range(self._line_count):
            level, component, context, detail = lines[i % len(lines)]
            events.append(
                LogEvent(
                    timestamp=start + step * (i + 1),
                    message=_render(component, context, detail),
                    level=level,
                )
            )
        if filter_pattern:
            events = [e for e in events if _matches(filter_pattern, e.message)]
        # Logs Insights sorts newest first; keep the same contract so callers see one shape.
        return list(reversed(events))
