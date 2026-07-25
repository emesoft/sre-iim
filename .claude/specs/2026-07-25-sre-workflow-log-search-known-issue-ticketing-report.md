# SRE Workflow: Log Search, Known-Issue Matching, Ticketing, Daily Report — Design

**Date:** 2026-07-25
**Status:** Implemented (branch `feature/cloudwatch-log-search`)
**Scope:** Backend (4 new capabilities on the existing FastAPI/Postgres/pgvector stack) and frontend
(IncidentDetail actions + a new Reports page).

**Note on older specs:** `SPEC.md`/`ARCHITECTURE.md`/`DATA_MODEL.md`/`PLAN.md`/`OPEN_QUESTIONS.md` describe
an earlier Phase-1 design — DynamoDB tables, a Lambda `AWSCollector` invoked automatically via
`CloudWatch Alarm → EventBridge`. The codebase moved to **Postgres + pgvector + FastAPI** before this
work started (see `CLAUDE.md`), and this feature set keeps that manual-trigger model: an SRE creates the
incident, then explicitly searches logs / resolves / files a ticket / pulls a report from the UI — there
is still no automated alarm → incident pipeline. Where this doc's decisions differ from those older specs
(e.g. ticketing is on-demand, not auto-gated on severity; log search is one HTTP call, not a Lambda
collector), this doc is authoritative for what's actually built; the older specs remain useful for the
original problem framing and the not-yet-built auto-ingest vision.

## Problem

The on-call flow: a CloudWatch alarm reaches the SRE via email/Slack (PagerDuty-style — log group,
condition, error message). They open IIM, create an incident, and want to (1) pull the real log lines
for that log group instead of pasting samples by hand, (2) know if this is a case they've already fixed
before, (3) get a tracking ticket filed with one click for a genuinely new error, and (4) roll up the
day's incidents into something postable to a Slack status channel.

## Goals

- Search CloudWatch Logs Insights for a log group + time window (+ optional error-message filter),
  merge the results into the incident and re-run analysis grounded in the real logs.
- Each project (`service`) can authenticate to its own AWS account via a named SSO profile — no shared
  static credentials, and the door is left open for Azure/GCP later without a redesign.
- Detect when a new incident matches a previously *resolved* incident (semantic similarity, not exact
  fingerprint match) and surface it as a known-issue banner instead of re-diagnosing from scratch.
- One click to file an Azure DevOps work item for a new/unseen error; keep the ticket URL on the incident.
- Generate a Slack-postable daily digest (counts + narrative) for a given date; copy-paste, no bot/webhook.

## Non-goals

- Automated alarm → incident ingestion (still manual; a future project once the owning teams grant access —
  see `CLAUDE.md`).
- Azure/GCP log-fetching or ticketing adapters (the ports are cloud-agnostic; only AWS/ADO are wired).
- Auto-posting the daily report to Slack (generate + copy only in this version).
- A UI for managing per-project cloud config (env vars only; see below).

## Multi-cloud / multi-project resolution

Projects will eventually span multiple clouds (AWS today; Azure, then GCP, are the stated roadmap), each
with its own account and credentials — so cloud-facing ports resolve **per project** (the incident's
`service`), not from one global setting.

- `infrastructure/config.py`: `ProjectConfig` (`cloud`, `aws_profile`, `aws_region`) +
  `get_project_config(service, settings)` reads `PROJECT_<SERVICE>_CLOUD` / `_AWS_PROFILE` / `_AWS_REGION`
  env vars. A project with no dedicated block falls back to the global `AWS_REGION` + default credential
  chain — additive, not a required setup step.
- AWS auth is **SSO profiles** from the operator's `~/.aws/config` (an AWS access portal with named
  per-account profiles, e.g. `GCM-Prod-ReadOnlyAccess`), not static keys. `docker-compose.yml` mounts
  `~/.aws` read-only into the backend container; the SRE runs `aws sso login --profile <name>` on the host
  before searching logs for that project.
- `infrastructure/logs/factory.py`: `build_log_fetcher(service, settings)` dispatches on `cloud`, raising
  `NotImplementedError` for a cloud not wired yet rather than silently falling back — a misconfigured
  `PROJECT_<SERVICE>_CLOUD` fails loudly.
- Azure/GCP credentials are explicitly deferred (not needed yet); the same per-project resolution pattern
  applies to `TicketClient` if ticketing ever needs to differ by project (not requested — ADO org/project
  are currently global settings).

## Feature 1 — CloudWatch Logs Insights search

- `domain/incidents/ports.py`: `LogFetcher` protocol — `fetch_logs(log_group, start, end, filter_pattern)`.
- `infrastructure/logs/cloudwatch_fetcher.py`: `CloudWatchLogFetcher` runs `StartQuery`/`GetQueryResults`;
  default query filters `ERROR|FATAL|Exception|Traceback|5\d\d`, or the SRE's own `filter_pattern` (the
  literal error-message text pasted from the alert — this is the field the frontend exposes as "Error
  message", pre-filled from `context.alert`/`sample_logs[0].message` when present).
- `POST /api/incidents/{id}/logs/search` fetches logs, merges them into `context["sample_logs"]`, persists
  `log_group` on the incident, and re-runs analysis via `IngestIncident.reanalyze_with_context` (new
  context → new fingerprint → fresh analyzer call, same cache-first semantics as ingest).
- Incident gains a nullable `log_group` column (migration `0003_incident_log_group`).

## Feature 2 — Known-issue matching + resolve

- Resolving an incident (`POST /api/incidents/{id}/resolve`, `ResolveIncident` use case) saves its case
  (summary + root_cause + recommended_action + resolution notes) as a `Document`
  (`source_type="incident"`, `documents.incident_id` linking back) through the existing chunk/embed/index
  pipeline — no new retrieval mechanism, it surfaces via the same `RagAnalyzer` retrieval every incident
  already runs.
- `RagAnalyzer` flags the top retrieved chunk as a known-issue match when `source_type == "incident"` and
  `similarity >= 0.85` (`KNOWN_ISSUE_SIMILARITY_THRESHOLD`), recording `known_issue_incident_id` /
  `known_issue_similarity` on the `Analysis` (migration `0004_known_issue_matching`, also adds
  `documents.incident_id`).
- Frontend: a known-issue banner on `IncidentDetail` links to the matched incident; a "Mark resolved"
  modal collects resolution notes.

## Feature 3 — Azure DevOps ticketing

- `domain/incidents/ports.py`: `TicketClient` protocol — `create_ticket(title, description) -> url`.
- `infrastructure/tickets/ado_client.py`: `AdoTicketClient`, a raw `httpx` call to the ADO Work Items REST
  API (PAT over HTTP Basic) — same raw-REST-over-SDK precedent as the DeepSeek/Jina adapters.
- `POST /api/incidents/{id}/ticket` builds title/description from the current `Analysis`, files the
  ticket, persists `ticket_url`, and moves status to `"ticketed"` (a value the status enum already
  reserved but nothing previously set) — migration `0005_incident_ticket_url`.
- Frontend: a "Create ADO ticket" action appears only when there's no known-issue match and no ticket yet
  (i.e. a genuinely new error); once created, the URL renders as a linked badge.

## Feature 4 — Daily SRE report

- `IncidentRepository.list_by_date_range(start, end)` (new port method + SQL impl).
- `application/incidents/daily_report.py`: `DailyReport.generate(date)` aggregates counts by
  severity/status in code, then makes one `ChatModel.complete(..., tier="fast")` call (the same generic
  port the multi-agent graph nodes use) to narrate a short Slack-Markdown digest.
- `GET /api/reports/daily?date=YYYY-MM-DD` returns counts + incident list + `slack_markdown`.
- Frontend: a new "Reports" page/nav entry — date picker, stat badges, incident list (with ticket links),
  and a "Copy for Slack" button (clipboard only — no bot/webhook).

## Incidental fix

`ErrorState` (the shared "backend unreachable" screen) was reused for every API failure, including a
downstream 500 (e.g. the Bedrock call failing for lack of real AWS credentials in a dev environment) —
telling the user to restart a backend that was already responding fine. `api.isUnreachable(e)` now
distinguishes a genuine network-level failure (no response at all) from an `ApiError` (backend responded,
just with a non-2xx), and every `ErrorState` call site passes the right copy.

## Verification

- Backend: `uv run pytest -q` (93 tests, including new unit tests with fakes for `LogFetcher`/
  `TicketClient`/`ChatModel`/`IncidentRepository`, plus HTTP-level tests for all 4 new endpoints) and
  `uv run ruff check .` — both clean.
- Frontend: `npm run typecheck` / `npm run build` — clean.
- Manual E2E via `docker compose up --build`: confirmed route wiring (OpenAPI schema), `log_group`/
  `ticket_url`/`known_issue` fields round-trip through `GET /api/incidents/{id}`, and the Reports page
  renders. Live CloudWatch/ADO calls need real AWS SSO / ADO PAT credentials to test end-to-end — not
  available in this dev environment; the code path was confirmed to reach the real API call (fails only
  on `NoCredentialsError`/`UnrecognizedClientException`, not a bug in this code).
