# IIM — Architecture (Phase 1)

- **Date:** 2026-07-17 · **Status:** Draft (design only) — the Lambda/EventBridge auto-trigger pipeline
  below was never built; the running app is a FastAPI/Postgres/pgvector backend the SRE drives manually
  (create incident → search logs / resolve / ticket / report, all on-demand from the UI). See
  [2026-07-25-sre-workflow-log-search-known-issue-ticketing-report.md](./2026-07-25-sre-workflow-log-search-known-issue-ticketing-report.md)
  for what's actually implemented around log search, known-issue matching, ticketing, and reporting.
- **Related:** [SPEC.md](./SPEC.md) · [DATA_MODEL.md](./DATA_MODEL.md) · [PLAN.md](./PLAN.md) · [OPEN_QUESTIONS.md](./OPEN_QUESTIONS.md)

## 1. Component overview

```
                          ┌─────────────────────────────────────────────┐
 CloudWatch Alarm         │              Analyzer Lambda (Python)         │
 (ALB 5xx / ECS mem)  ─┐  │  1. collect_context()  [read-only AWS APIs]   │
 Logs Metric Filter    ├─▶│  2. fingerprint + cache lookup (DynamoDB)      │──▶ Bedrock
 (FATAL / OOMKilled)  ─┘  │  3. analyze()  [backend/ai/analyze_incident.py]  ◀─────┼──  (Haiku 4.5,
        │  (EventBridge)  │  4. write incident + cache (DynamoDB)          │    Sonnet 5 opt.)
        │                 │  5. create/dedup ticket (if severity=critical) │
   (SNS: human email)     └───────────────┬───────────────────────┬───────┘
                                           │                       │
                                   DynamoDB │                       │ Azure DevOps
                              iim-incidents │                       │ REST (one-way)
                              iim-cache      │                       │ PAT ← Secrets Manager
                                           │
   Board (React on S3)  ◀── API Gateway ◀── Read Lambda ◀───────────┘
   3-column single pane      (GET /incidents, /incidents/{id})
```

## 2. Data flow (happy path)

1. A CRITICAL-class signal fires a **CloudWatch Alarm** (metric) or a **Logs Metric Filter** alarm
   (pattern `FATAL` / `OOMKilled` / `OutOfMemoryError`).
2. The alarm reaches the **Analyzer Lambda** via **EventBridge** (see ADR-005). SNS may also fan out a
   human email notification.
3. The Lambda calls `AWSCollector.collect_context(event)` → an `IncidentContext` (read-only APIs).
4. It computes `fingerprint(context)` and does a **cache lookup** in `iim-cache`.
   - **Hit** (unexpired): reuse the stored analysis — no LLM call.
   - **Miss:** call `analyze(context)` (Bedrock), then write the result to `iim-cache` with a TTL.
5. It writes one **incident record** to `iim-incidents` (with `cache: HIT|MISS`).
6. If AI `severity == critical`: **create or dedup** an Azure DevOps Bug (comment on recurrence).
7. The **board** (React/S3) reads incidents through **API Gateway → Read Lambda → DynamoDB**.

## 3. Components

| Component | Tech | Responsibility |
|---|---|---|
| Analyzer Lambda | Python (Bedrock via `boto3` Converse) | Collect context, cache-check, analyze, persist, ticket |
| Read Lambda | Python | Serve board reads from DynamoDB (`GET /incidents`, `GET /incidents/{id}`) |
| API Gateway | HTTP API | Public read endpoint for the board (no auth — non-goal) |
| DynamoDB | `iim-incidents`, `iim-cache` | Incident occurrences + fingerprint cache/dedup (TTL) |
| Bedrock | Claude Haiku 4.5 (default) | Reasoning; Sonnet 5 escalation optional (ADR-008) |
| S3 (+ CloudFront if HTTPS needed) | React SPA | Single-pane-of-glass board |
| Azure DevOps | REST API 7.1, PAT in Secrets Manager | One-way Bug creation + recurrence comments |
| Sandbox ECS service | Fargate, JSON logs w/ `level` | The one demo service `GCM` + a "break it" control |

## 4. Trigger path

**Baseline (per brief):** `Alarm → SNS → EventBridge → Lambda`.
**Recommended (ADR-005):** `Alarm state-change → EventBridge rule → Lambda`, keeping **SNS only for human
email**. Fewer hops, same behavior. Final choice tracked in [OPEN_QUESTIONS.md](./OPEN_QUESTIONS.md).

Alarms configured (sandbox):
- **Metric alarm:** ALB `HTTPCode_Target_5XX_Count` rate, and ECS memory utilization.
- **Logs metric filter → alarm:** patterns `FATAL`, `OOMKilled`, `OutOfMemoryError` on the `GCM` log group.

## 5. Severity tiering + LLM gating

Two distinct notions of severity:

- **Alert tier (what fires the pipeline):** only **CRITICAL-class** alarms invoke the Analyzer Lambda.
  ERROR/WARN signals do not trigger the pipeline in Phase 1 — this is the primary cost control (the LLM is
  never called for low-severity noise).
- **AI-assessed severity (in the JSON):** `critical | warning | info`, decided by the model from the
  gathered evidence. This gates downstream action:

| AI `severity` | Board | Ticket |
|---|---|---|
| `critical` | shown 🔴 | create ADO Bug (dedup by fingerprint) |
| `warning` | shown 🟡 | no ticket |
| `info` | shown ⚪ | no ticket |

This lets the AI **downgrade a noisy alarm** (e.g. a transient blip) to `info` and suppress ticket spam —
a concrete "effective AI use" behavior.

## 6. Error handling

- **Collector partial failure:** if one AWS call fails (e.g. Logs Insights timeout), collect what
  succeeded and pass a partial `IncidentContext`. `build_user_message()` already prints only present
  fields, and `SYSTEM_PROMPT` instructs the model to state what is missing rather than hallucinate.
- **LLM/JSON parse failure:** `analyze()` returns `{"error": ..., "raw": ...}`; the incident is still
  written (board shows a degraded card) and no ticket is created. Retry is manual in Phase 1.
- **Ticket creation failure:** logged; the incident record still persists. The ADO call is best-effort and
  never blocks the board write.
- **Idempotency:** the incident write is keyed by a stable `incident_id` derived from the triggering event
  so a retried Lambda invocation does not create duplicates.

## 7. Security

- Analyzer Lambda IAM: **read-only** — `ecs:Describe*`, `elasticloadbalancing:Describe*`,
  `cloudwatch:GetMetricData`, `logs:StartQuery` / `logs:GetQueryResults`, plus `dynamodb:PutItem/GetItem`
  on the two tables and `secretsmanager:GetSecretValue` on the ADO PAT secret only.
- No secrets in git (`.gitignore` enforced). ADO PAT lives in Secrets Manager.
- Board API is unauthenticated (internal prototype, explicit non-goal) — do not expose real data through it.

## 8. Architecture Decision Records

Each ADR: **Context → Decision → Consequences**.

### ADR-001 — Serverless (Lambda) over a long-running server
- **Context:** event-driven workload (alarms are sporadic); low-cost requirement.
- **Decision:** AWS Lambda for both the analyzer and the board read API.
- **Consequences:** near-zero idle cost; natural fit for EventBridge triggers; cold-start latency is
  acceptable for an incident tool (seconds). No 24/7 compute to pay for or babysit.

### ADR-002 — Amazon Bedrock over an external LLM API
- **Context:** "AWS-native, no third parties" differentiator; data stays in AWS.
- **Decision:** call Claude on **Amazon Bedrock** (`boto3` Converse API).
- **Consequences:** context never leaves AWS; IAM-based access; region `ap-southeast-1` requires a regional
  **inference-profile** model id (e.g. `apac.anthropic.claude-...`) — exact string confirmed in the Bedrock
  console (see OPEN_QUESTIONS). Trade-off: model availability is gated by Bedrock Model access.

### ADR-003 — DynamoDB for state + cache
- **Context:** need persistence for incidents and a TTL-based cache; low cost; serverless.
- **Decision:** two tables — `iim-incidents` and `iim-cache` — with DynamoDB TTL on the cache.
- **Consequences:** pay-per-request, no idle cost; native TTL auto-expiry (best-effort, up to ~48h late, so
  the app also checks `ttl` on read). See [DATA_MODEL.md](./DATA_MODEL.md).
- **Alternative rejected:** single-table design — over-engineering for a one-service prototype.

### ADR-004 — Collector abstraction for multi-cloud readiness
- **Context:** future potential = "just write a new collector" for another cloud.
- **Decision:** a `Collector` Protocol with a single `collect_context(event) -> IncidentContext` method;
  Phase 1 ships only `AWSCollector`. `IncidentContext` is pinned by the existing `build_user_message()`
  contract in `backend/ai/analyze_incident.py`.
- **Consequences:** the AI layer is cloud-agnostic; adding Azure later touches only a new class. No Azure
  code is written now (non-goal).

### ADR-005 — Trigger path
- **Context:** the brief draws `Alarm → SNS → EventBridge → Lambda`.
- **Decision:** implement `Alarm state-change → EventBridge → Lambda` and keep **SNS for human email only**;
  fall back to the full SNS-in-path chain if preferred.
- **Consequences:** one fewer hop and less IAM surface; SNS still available for notifications. Pending
  confirmation in OPEN_QUESTIONS.

### ADR-006 — Cache-first design
- **Context:** save tokens; avoid re-analyzing and re-ticketing the same incident.
- **Decision:** `fingerprint = service + normalized-error-signature + deploy-version`; look up `iim-cache`
  before calling the LLM; a different deploy version → different fingerprint → forced re-analysis.
- **Consequences:** recurring incidents return instantly with no LLM cost; the same fingerprint record
  also backs ticket dedup (`ticket_id`). A stale cache cannot misdiagnose a post-deploy incident.

### ADR-007 — Alert severity tiering
- **Context:** cost control + ticket-noise control.
- **Decision:** only CRITICAL-class alarms fire the pipeline; the AI `severity` field then gates ticketing
  (`critical` → Bug; `warning`/`info` → board only). See §5.
- **Consequences:** the LLM runs only when warranted; the AI can suppress tickets for benign alarms.

### ADR-008 — Model tiering
- **Context:** balance cost and reasoning quality.
- **Decision:** **Claude Haiku 4.5** is the default for all Phase-1 incidents. An optional, config-gated
  escalation (off by default) re-runs once with **Claude Sonnet 5** when Haiku returns `confidence = low`
  on a `critical` incident.
- **Consequences:** low steady-state cost; a clean upgrade path for hard cases without always paying
  Sonnet prices. Exact Bedrock model ids confirmed in console (OPEN_QUESTIONS).
