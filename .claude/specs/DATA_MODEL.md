# IIM — Data Model & Interfaces (Phase 1)

- **Date:** 2026-07-17 · **Status:** Draft (design only) — **superseded by the actual Postgres/pgvector
  schema** (`backend/app/infrastructure/db/orm.py` + `backend/migrations/`); the DynamoDB tables below were
  never built. `ticket_url` and the CloudWatch log-search collector described here **are** now
  implemented, just on Postgres with a manual (not Lambda/EventBridge-triggered) flow — see
  [2026-07-25-sre-workflow-log-search-known-issue-ticketing-report.md](./2026-07-25-sre-workflow-log-search-known-issue-ticketing-report.md).
- **Related:** [SPEC.md](./SPEC.md) · [ARCHITECTURE.md](./ARCHITECTURE.md) · [PLAN.md](./PLAN.md)

## 1. DynamoDB tables

Two tables. Both on-demand (pay-per-request). Region `ap-southeast-1`.

### 1.1 `iim-incidents` — one item per incident occurrence

What the board reads. A new item is written on **every** occurrence, including cache hits (so the board
shows recurrences and the "cache HIT" speed difference).

| Attribute | Type | Notes |
|---|---|---|
| `incident_id` (PK) | S | Stable id derived from the triggering event (idempotent on Lambda retry) |
| `service` | S | e.g. `gcm` |
| `cluster` | S | e.g. `prod-fargate` (optional) |
| `started_at` | S | ISO 8601 — when the anomaly started |
| `fingerprint` | S | See `fingerprint()` in `backend/ai/analyze_incident.py` |
| `severity` | S | AI-assessed: `critical` \| `warning` \| `info` |
| `summary` | S | AI: 1–2 sentences |
| `root_cause` | S | AI: reasoning with evidence |
| `recommended_action` | S | AI: concrete next action |
| `confidence` | S | AI: `high` \| `medium` \| `low` |
| `context` | M | The raw `IncidentContext` that was analyzed |
| `cache` | S | `HIT` \| `MISS` |
| `model_id` | S | Bedrock model that produced the analysis |
| `ticket_url` | S | ADO work-item URL, if a ticket was created (optional) |
| `created_at` | S | ISO 8601 — when IIM processed it |
| `ttl` | N | Epoch seconds; optional housekeeping expiry (e.g. 7 days) so the prototype table doesn't grow forever |

**GSI `by_service`** — PK `service`, SK `started_at`. Access pattern: *"list recent incidents for `GCM`,
newest first"* for the board's left column.

**Access patterns**
- Board list → Query `by_service` (PK=`gcm`), `ScanIndexForward=false`, limit N.
- Board detail → `GetItem(incident_id)`.
- Write → `PutItem` (idempotent by `incident_id`).

### 1.2 `iim-cache` — fingerprint cache + ticket dedup

Replaces the in-memory `_CACHE` in `backend/ai/analyze_incident.py` (Step 4). Also backs ticket dedup.

| Attribute | Type | Notes |
|---|---|---|
| `fingerprint` (PK) | S | Cache key |
| `analysis` | M | The 5-field AI result (see §3) |
| `model_id` | S | Model that produced it |
| `created_at` | S | ISO 8601 |
| `ttl` | N | **DynamoDB TTL attribute** — epoch seconds = created + `CACHE_TTL_SECONDS` (1800) |
| `ticket_id` | S | ADO work-item id, if one exists for this fingerprint (dedup) |
| `ticket_url` | S | ADO work-item URL |

- **TTL** is enabled on `ttl`. DynamoDB deletes expired items within ~48h, **not exactly at expiry**, so the
  Lambda also compares `ttl` to now on read and treats an expired item as a miss.
- **Cache hit:** `GetItem(fingerprint)` returns an unexpired item → reuse `analysis`, no LLM call.
- **Ticket dedup:** before creating a Bug, check `ticket_id`; if present, add a comment to that work item
  instead of creating a new one.

## 2. `IncidentContext` — the collector output shape

The shape is **pinned by the existing `build_user_message()` contract** in `backend/ai/analyze_incident.py`: every
field below is one the AI layer already reads. All fields except `service` are optional — the collector
emits only what it gathered, and `build_user_message()` prints only present fields (handles both infra and
non-infra incidents). Proposed as a `TypedDict` (or dataclass):

```python
class Ecs(TypedDict, total=False):
    running: int
    desired: int
    stopped_reason: str
    restarts_5m: int
    task_memory: str

class Alb(TypedDict, total=False):
    healthy: int
    total: int
    error_rate: str      # e.g. "23.7%"
    p95_latency: str     # e.g. "4.8s"
    rpm: int

class RecentDeploy(TypedDict, total=False):
    service: str
    version: str
    by: str
    relative_time: str   # e.g. "3 min before incident"

class SampleLog(TypedDict, total=False):
    ts: str
    level: str
    message: str
    count: int           # repeat count for collapsed identical lines

class IncidentContext(TypedDict, total=False):
    service: str         # required in practice
    cluster: str
    started_at: str
    alert: str           # human description (used for non-infra incidents)
    ecs: Ecs
    alb: Alb
    recent_deploy: RecentDeploy
    metrics: dict[str, str | int]
    sample_logs: list[SampleLog]
    runbook: str
```

The canonical examples of two valid shapes are the sample files:
- Infrastructure: `backend/ai/samples/infra_oom.json` (`ecs`, `alb`, `metrics`, `sample_logs`, `recent_deploy`).
- Non-infrastructure: `backend/ai/samples/apicost_overage.json` (`alert` + `metrics` / `sample_logs`).

## 3. AI analysis result shape

Produced by `analyze()` (Bedrock), validated against `SYSTEM_PROMPT`'s schema:

```json
{
  "severity": "critical | warning | info",
  "summary": "1-2 sentences: what is happening and the impact",
  "root_cause": "reasoning with evidence and timestamps; if uncertain, the most likely hypothesis + confidence",
  "recommended_action": "concrete action to take now, prioritising stopping the damage first",
  "confidence": "high | medium | low"
}
```

At runtime `analyze()` also attaches a non-persisted `_cache` marker; the Lambda maps that to the
incident's `cache` attribute.

## 4. Collector interface

```python
class Collector(Protocol):
    def collect_context(self, event: dict) -> IncidentContext:
        """Gather read-only context for the service referenced by `event`."""
        ...
```

Phase 1 ships one implementation:

```python
class AWSCollector:
    def collect_context(self, event: dict) -> IncidentContext:
        # read-only: ECS Describe*, ELBv2 DescribeTargetHealth,
        # CloudWatch GetMetricData, Logs StartQuery/GetQueryResults (filter on `level`),
        # recent deploy from the ECS service's deployment / task-def revision.
        ...
```

- `event` is the EventBridge-delivered alarm event; it carries the alarm name from which the target
  service (`GCM`) and log group are resolved (config-driven for the single Phase-1 service).
- Multi-cloud later = a new class implementing the same `Collector` Protocol. No Azure code in Phase 1.

## 5. Logs Insights query (because logs are JSON with `level`)

Because the demo service emits **structured JSON with a `level` field**, the collector filters on the
structured field rather than regex-matching raw text — cleaner and cheaper:

```
fields @timestamp, level, @message
| filter level in ["ERROR", "FATAL"]
| sort @timestamp desc
| limit 20
```

Identical repeated lines are collapsed with a `count`, matching the `SampleLog.count` field the AI layer
already understands.
