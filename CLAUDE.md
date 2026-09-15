# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**LLM-SRE** (internal name **IIM**) is an AI incident-analysis layer for SRE / on-call work. It feeds an
automatically-collected incident context bundle into an LLM (AWS Bedrock, Claude models) and returns a
structured triage analysis as JSON: `severity`, `summary`, `root_cause`, `recommended_action`, `confidence`.

The full stack now exists: FastAPI backend (`backend/app/`) with pgvector-backed persistence, a React
frontend (`frontend/`), and a LangGraph multi-agent analysis pipeline — `backend/ai/analyze_incident.py` was
the original Step-0 prototype and is superseded by `backend/app/infrastructure/graph/analyzer.py` /
`backend/app/infrastructure/llm/` for anything running through the app. Current backend routes
(`backend/app/interface/http/`):

- `POST /api/incidents`, `GET /api/incidents`, `GET /api/incidents/{id}` — ingest / list / detail.
  The list is ordered open-first, then newest, and capped at one page (`limit`, default 50) — so
  **never derive a total from it**; that's what the rollup below is for.
- `POST /api/incidents/bulk` — one action (`resolve` | `analyze`) over several incidents; each id
  is scope-checked individually and skipped rather than failing the batch. Capped at 25 ids, and
  `analyze` at 10, because every id there is a paid LLM call.
- `GET /api/incidents/rollup` — dashboard aggregates computed in SQL (active / urgent / untriaged /
  intake in the last 24h, per-project counts, and the noisiest fingerprints in the window). Every
  number the dashboard and the top bar's alert badge show comes from here.
- `GET /api/incidents/{id}/stream` — **SSE**, live analysis progress (implemented, not just planned)
- `POST /api/incidents/{id}/resolve` — mark resolved, feeds back into RAG as a known-issue case

**One incident means one *chain*, everywhere.** A recurring alarm links each firing to the last via
`previous_incident_id`, and both the lists and the rollup counts apply `_not_superseded()` so they
describe the same set — the tab once read "Resolved 19" above nine rows, because the count was raw
and the table collapsed chains. Keep any new count on that predicate.

**Triage lanes** (`domain/incidents/lanes.py`) group statuses by what they ask of a person, which
is a different question from where the incident sits in the pipeline: `new` and `failed` are at
opposite ends of the latter and identical in the former (nobody has looked at this). The lane map
lives in the domain because three things read it — the `?lane=` list filter, the rollup's `lanes`
counts, and the UI tabs — and three private opinions about which statuses belong together would
drift. The Incidents page fetches **per lane** rather than filtering the dashboard's page of rows:
a "Needs triage 40" tab filtered from a 50-row page would show whichever twelve happened to land
on it.

**Analysis runs as an in-process background task** (`asyncio.create_task(_run_analysis(...))`),
which means a restart mid-run orphans the incident: the row still says `analyzing`, the error path
that would have marked it `failed` never gets to run, and nothing else notices. `_requeue_interrupted`
in main.py sweeps rows stuck in `analyzing` for longer than `ANALYSIS_STALE_MINUTES` (15) back to
`new` — at startup *and* on every poll cycle, so a crash at 02:00 recovers by 02:05 rather than by
whoever notices in the morning. The age threshold is what keeps it safe if a second replica is
mid-analysis.

That sweep depends on `updated_at` moving when the status does. It didn't until 2026-09-13 —
`_utcnow_column()` only set a `server_default`, so `updated_at` silently meant `created_at` on
incidents, documents and integrations alike. Any staleness check reading it was wrong by the age
of the row; the sweep would have requeued a run that had just started on a day-old incident and
paid for it twice. `_utcnow_column(on_update=True)` is now used for every `updated_at`, and
`tests/test_updated_at_stamping.py` pins it at the compiled-SQL level. No migration needed —
`onupdate` is emitted by SQLAlchemy, not stored in the schema.
- `POST /api/incidents/{id}/ticket` — creates a **real** Azure DevOps work item via PAT (not a stub)
- `GET /api/incidents/{id}/chat`, `POST /api/incidents/{id}/chat` — chat about an incident; Claude
  can autonomously call a `fetch_logs` tool via MCP tool-calling (`claude_cli` provider only — 501
  otherwise)
- **New Relic polling asks for an explicit 7-day `timeWindow` and follows `nextCursor`.**
  `aiIssues.issues` applies its own default window of roughly a day: a live account returned 4
  issues unwindowed and 40 over seven days, so `CRITICAL - SES Errors` — open and firing for two
  days — never reached the poller and no incident was ever created for it. Nothing errored; a short
  list is indistinguishable from a quiet account, which is the same reason the query pages.
- `POST /api/documents`, `GET /api/documents` — knowledge-base ingest (chunk + embed) / list
- `GET /api/reports/daily` — Slack-markdown daily digest
- `POST /api/auth/login` (username + password), `POST /api/auth/entra` (Microsoft Entra ID token
  -> this app's JWT), `GET /api/auth/entra/config` (unauthenticated: tells the sign-in screen
  whether to show the Microsoft button)
- `GET /healthz`

**Which model runs the analysis is a per-project choice**, made on Settings → AI & usage. The unit
is a **model profile**: a named (provider + settings + credential), e.g. "Anthropic — prod key".
One profile is the default; a project may point at another. Projects *reference* profiles rather
than carrying their own copy of a credential — so one key serving ten projects is stored once and
rotated once, and "what is this key used for" is answerable. Don't move the provider/key onto the
project rows.

- `infrastructure/llm/catalog.py` declares the providers (Anthropic API key, AWS Bedrock, Claude
  Code subscription, any OpenAI-compatible endpoint) and the fields each needs, so the form builds
  itself and adding a provider is one catalog entry plus an adapter.
- `infrastructure/llm/factory.py` owns storage and resolution: `resolve(session, settings, enc,
  project)` → `LlmProfile` → `build_analyzer` / `build_chat_model`. With nothing stored it
  **falls back to `LLM_PROVIDER` and the env models**, so an instance that never opens the page
  behaves as it always did.
- Storage is the `app_settings` row `llm_config`, one encrypted JSON blob (`profiles`,
  `default_profile_id`, `by_project`). The Claude Code OAuth token stays under its own
  long-standing key, which the CLI adapter reads directly — two copies of a credential is how they
  drift. `_migrate()` converts the short-lived first shape on read.
- **Model ids are picked, not typed.** Each provider's `model`/`fast_model` field carries its own
  `options` list (CLI aliases `opus|sonnet|haiku`, API ids `claude-opus-5…`, Bedrock ids with their
  regional inference-profile prefixes) and the form renders a dropdown with an "Other…" escape
  hatch. The list is deliberately **not** enforced server-side — a model released this morning must
  be usable this morning, and a shipped container can't know about it. What catches a hand-typed
  mistake instead is `last_test_ok`: a profile shows **never tested** until a real call has
  answered, and any edit to the provider, the model or the credential clears a previous pass, since
  a green tick left over from the old value certifies something that was never run.
- Secrets are write-only: the API returns `secret_names`, never values, and a save without a secret
  keeps the stored one. Bedrock can borrow an existing AWS **integration's** credentials rather
  than a second copy of the same keys.
- **Precedence is project override > requesting group's profile > default** (migration 0026 adds
  `groups.model_profile_id`). The order is deliberate and load-bearing: a per-project profile says
  where a customer's data is *allowed* to go, a per-group one says who pays — a constraint outranks
  a preference, so a team's own key can never pull a pinned project's incidents out of the account
  they were promised to stay in. Background work (the poll job, auto-analysis) has no requester, so
  it resolves with no group profile.
- **Attribution**: `analyses.llm_profile` records the profile that ran each analysis and the usage
  rollup groups by (model, profile) — two cohorts on two keys running the same model would
  otherwise collapse into one line. `AttributedAnalyzer` stamps it, because provider adapters are
  handed a key and a model id and have no idea profiles exist. Spend from before any profile
  existed stays NULL rather than being attributed by guesswork.
- Because the model depends on the incident's project, `IngestIncident` takes an
  **`AnalyzerSelector`** (`domain/incidents/ports.py`), not an `Analyzer` — the choice can only be
  made once the incident is known. `FixedAnalyzer` is the one-analyzer implementation used by
  tests; `ConfiguredAnalyzers` (deps.py) is the real one, and it deliberately reuses the injected
  default analyzer when a project has no override so test overrides of `get_base_analyzer` still
  reach everything.

**Autoscaling alarms are suppressed at ingest** (`domain/integrations/noise.py`). AWS Application
Auto Scaling creates a `TargetTracking-<resource>-Alarm{High,Low}-<uuid>` pair per policy, and the
low one sits in ALARM for as long as a service is comfortably under target — the normal state of an
idle service, and 6 of this account's 26 distinct alarm names. They are tracked (so the state isn't
re-evaluated every cycle) but never become incidents. The pattern requires the trailing UUID, which
is what makes it a judgement about *AWS's generated names* rather than about names a person chose:
a hand-named alarm is never silently dropped. `PollAlarmsJob.suppress_scaling_noise` turns it off.

**Chat runs on the Messages API when the profile is Anthropic** (`llm/anthropic_chat.py`), and only
falls back to the Claude Code CLI for the subscription profile. Measured reason: a bare
`claude -p "say ok"` — no MCP config, no system prompt, no context — costs **~29,000 tokens** of the
CLI's own coding-agent harness, against **~900** for everything this feature needs (incident
context ~157, chat system prompt ~354, four tool schemas ~400). About 97% of every chat turn was
harness, re-paid on each internal step of a tool-using turn, which is how single turns reached 95k
and 135k. The API path runs the tool loop itself (`tool_use` → run → `tool_result`), keeps the
transcript in our own `chat_messages` rather than a provider-side session, and is bounded by
`_MAX_TOOL_ROUNDS` — on exhaustion it says so rather than returning a half-thought as an answer.
`incident_tools.py` holds the four tools once, described both as MCP tools and as Messages-API
schemas, so the two backends can't drift.

**Incident chat can investigate too, not just read.** `mcp_log_tool.py` serves `fetch_logs`,
`describe_alarm`, `metric_datapoints` and `ecs_service_state` over MCP, scoped to one project by
`IIM_INCIDENT_SERVICE` (an env var, never a tool parameter, so Claude cannot be talked into reading
another project's account). Two things must stay in sync or a tool is **silently** uncallable: it
has to be registered on the server *and* listed in `_ALLOWED_TOOLS` in `claude_cli.py`
(`--allowedTools`). When that broke, chat answered an ECS question by explaining it had no AWS CLI
and pasting the `aws ecs describe-services` commands for the reader to run — no error anywhere.
`tests/test_chat_tools.py` pins both directions, and the chat system prompt names each tool and
forbids handing the work back.

**Token accounting keeps fresh and cached input apart** (migration 0027). `input_tokens` used to be
`input + cache_read + cache_creation` summed, which reported a chat turn as "92,897 in" when 2
tokens were fresh — the rest being the system prompt, incident context and transcript re-read from
cache on every turn, at a fraction of the price. Read as spend that is wrong by roughly an order of
magnitude, and summing the column across turns counts the same prefix repeatedly. So: `input_tokens`
is fresh only, `cached_input_tokens` holds the rest, and rows written before the split report under
`unsplit_input_tokens` rather than being counted as fresh. The usage table also now includes
**incident chat**, which it previously ignored entirely while chat was the larger share of the spend.

**Evidence is gathered before the model is asked anything.** `infrastructure/cloud/aws_enricher.py`
(the `ContextEnricher` port) looks up what an analyst would: the alarm's own definition and
`StateReason`, real metric datapoints for the last hour, and for an ECS alarm the desired/running
task counts, stop reasons and recent service events. Without it the honest ceiling on an analysis
is a hypothesis plus a checklist for a human — which is a summary, not an investigation.

Three rules hold it together:

- **`IngestIncident.enricher` has no default, on purpose.** It shipped with one and not a single
  one of the five construction sites passed it — the HTTP route, the background resolver, the poll
  job and both scheduler paths all built an ingest that silently skipped enrichment, so the feature
  was dead in production while every test passed (tests build the class directly and did pass one).
  A required argument makes that a TypeError. `tests/test_ingest_wiring.py` also sweeps the app for
  any `IngestIncident(` built without one, since the failure mode is a *new* call site.
- **It never breaks analysis.** Every lookup is individually guarded, and `IngestIncident._enrich`
  guards the whole call again — "never raises" is a guarantee the use case depends on, so it
  enforces it rather than trusting each implementation. An expired SSO session costs a section of
  evidence, not an incident.
- **Dimensions come from `describe_alarms`, never from the alarm name.** `ECS-CPUReservation-ecs-evp
  -datalink-qa` looks parseable and isn't — the cluster and service in it are a naming convention.
- **Only on a cache miss, and the fingerprint is left alone.** A cache hit spends no LLM call, so it
  spends no API calls either; and re-deriving the fingerprint from live values that move every
  minute would mean the cache never hits again. The enriched context *is* persisted, so the detail
  view shows the same facts the analysis cited — an analysis citing numbers nobody else can see
  isn't reviewable.

`ANALYSIS_MODE` (docker-compose.yml) selects the analyzer: `single` (default) = one RAG-augmented LLM call;
`graph` = LangGraph multi-agent (triage → retrieve → diagnose → critic → loop up to `MAX_ROUNDS` →
synthesize) with fast/main model tiering. 21 test files under `backend/tests/` cover HTTP routes, SSE, the
graph analyzer, RAG, daily report, and the ADO client.

## Running

In the target architecture the analysis brain is **not run by hand** — the backend app (FastAPI)
imports it as a library and invokes it through the LangGraph multi-agent graph when an incident is
ingested (`POST /api/incidents`). The app is the entry point:

```bash
# Full stack — db (pgvector) + backend (FastAPI) + frontend (nginx static build).
# docker-compose.yml lives at the repo root, alongside backend/ and frontend/:
docker compose up --build
# backend only, for development:
cd backend && uv sync && uv run uvicorn app.main:app --reload
```

Until that backend exists, the Step 0 brain can be exercised directly on a sample — a **dev/debug
harness only**, not how the app runs in production:

```bash
pip install boto3
export AWS_REGION=ap-southeast-1        # account/role must have Bedrock access to the model
python backend/ai/analyze_incident.py backend/ai/samples/infra_oom.json        # infrastructure (OOM/5xx)
python backend/ai/analyze_incident.py backend/ai/samples/apicost_overage.json  # non-infrastructure (API cost)
```

Running a sample calls the LLM once (cache miss), then re-runs the same incident to demonstrate a cache
hit (0 tokens). There is no test suite yet (`.gitignore` anticipates `pytest`).

- **Model / region**: set in constants at the top of `analyze_incident.py` (`MODEL_ID`, `REGION`). Defaults
  to a Haiku model for cost. Region `ap-southeast-1` may require a Bedrock *inference profile* prefix
  (e.g. `apac.anthropic.claude-...`) rather than the bare model id — confirm against the Bedrock console
  (Model access) if a call fails.

## Testing — never run DB-touching pytest against the live dev database

`backend/tests/` has real integration tests (`test_*_http.py`, `test_*_repository.py`) that connect
to a **real Postgres** via `TEST_DATABASE_URL`/`DATABASE_URL`, and their fixtures **delete rows**
from tables they touch (`integrations`, `projects`, `documents`, `incidents`,
...) before each test run. This is the same Postgres the `docker compose` stack uses for the app a
person is actually looking at in their browser — there is no separate, automatically-isolated test
database.

This bit hard once already: a session ran the full suite against the live dev DB mid-development,
which silently wiped the real connection rows (encrypted AWS/ADO credentials,
unrecoverable) and left fake test-generated incidents/documents visible in the running UI.

Before running (or asking an agent to run) any `pytest` command that resolves a real
`DATABASE_URL`/`TEST_DATABASE_URL` in this repo:
- Confirm with the user first, unless they've explicitly asked for a full test run against a
  database they know is disposable.
- Prefer `ruff check`, `npm run build`, or unit tests that use fakes/mocks (no DB) to verify a
  change instead.
- If DB tests must run, treat it as a write operation against potentially-live data and say so
  explicitly before running — don't assume a database named "test" or a `TEST_DATABASE_URL` env
  var actually points somewhere disposable.

Separately: every DB-touching test file's own hardcoded fallback connection string
(`postgresql+asyncpg://iim:iim@localhost:5432/iim`) uses the **wrong password** for this repo's
actual dev Postgres (see `docker-compose.yml` / `.env`'s `DB_PASSWORD`, default `change-me`).
Running `pytest` without an explicit, correct `DATABASE_URL` doesn't fail loudly — it silently
**skips** every DB-touching test (the fixture's connection attempt catches the error and calls
`pytest.skip`), producing a falsely reassuring "0 failed" result while testing almost nothing.
Always pass the real password explicitly when a DB test run is actually warranted.

## Frontend (`frontend/` — local test UI)

A **Vite + React + TypeScript + Tailwind** single-page app that exercises the backend REST API
end-to-end: submit incidents, seed knowledge documents, and view the AI analysis plus the evidence
chunks it cited. It consumes the backend's SSE stream live (`src/lib/useIncidentStream.ts`) for analysis
progress. It is a **local test/development UI** for iterating on the pipeline — deliberately lean (no
auth, no automated tests). Full design and task breakdown live in
`.claude/specs/FRONTEND_LOCAL.md` and `.claude/specs/FRONTEND_LOCAL_PLAN.md`; run instructions are in
`frontend/README.md`.

```bash
# Option A — full stack in Docker, from the repo root (no cd needed):
docker compose up --build                # db + backend :8000 + frontend :5173

# Option B — frontend with hot reload, backend still via Docker:
docker compose up db backend             # db (pgvector) + backend on :8000
cd frontend && npm install && npm run dev  # Vite on :5173, proxies /api -> :8000
```

Open **http://localhost:5173**. In dev mode, Vite proxies `/api` and `/healthz` to `:8000`
(`vite.config.ts`); the Docker image serves the production build via nginx, which proxies the same two
paths to the `backend` service (`frontend/nginx.conf`) — either way the app talks to a relative base and
needs no CORS config. The Docker image has no HMR, so keep using `npm run dev` (Option B) while actively
changing frontend code; `up --build` is for full-stack smoke-testing or demos. Structure:

- **`src/lib/`** — `api.ts` (thin fetch wrapper; throws `ApiError` carrying the backend `detail`, plus
  `errText()` to normalize any thrown value — network `TypeError`, non-JSON proxy error page, or
  `ApiError` — into one display string), `types.ts` (mirrors the backend DTOs — the source of truth for
  request/response shapes), `theme.ts` (light/dark), `severity.ts` (severity → fixed status palette),
  `status.ts` (incident lifecycle status → pastel badge tone), `format.ts` (`timeAgo`, `incidentRef`),
  `nav.ts` (sidebar sections + the three views), `useDashboard.ts` (one shared fetch of incidents +
  documents, keyed by a `refreshKey` the App bumps after any ingest — Overview, Incidents, and the top
  bar's alert count all read from this single hook so they never disagree).
- **`src/components/`** — `layout/` (`Sidebar` — dark nav rail with sectioned nav; `TopBar` — search,
  notification bell keyed off urgent-incident count, health pill, theme toggle; `PageHeader` — per-view
  title + primary action) and `ui/` (hand-rolled primitives: Button, Card, Badge, SeverityBadge,
  StatusBadge, Modal, `Skeleton` (loading shimmer), `EmptyState`, `ErrorState` (the designed
  "can't reach the backend" screen — shows the fix command + Retry), `BrandMark` (the real Emesoft
  logo, sign-in screen only), …). Alongside them: `Sparky` (the product mascot — a firefighter
  robot drawn as flat SVG in the theme's accent tokens, with `idle`/`alert`/`happy` moods driven by
  real state; it's the app's mark in the nav rail, favicon, empty/error screens and the sign-in
  diagram), `AttentionCard` + `StatStack` (the dashboard's triage strip — one hero card for what
  needs a decision, compact rows for the supporting numbers), `ProjectBoard` + `NoisyAlarms` (the
  half of the dashboard that survives hundreds of alerts: one row per project and per repeating
  fingerprint, both fed by the rollup endpoint, so neither grows with the incident count — clicking
  a project opens the incident list already scoped to it), `ActivityFeed` (a real timeline built
  from incident-ingested / document-indexed events, collapsing adjacent repeats) and
  `OrchestrationMap` (the sign-in screen's inputs → AI core → outputs diagram, one hand-laid-out
  SVG — see its docstring before moving anything in it). No component library.
- **`src/pages/`** — `Overview` (triage strip + recent incidents + activity feed dashboard),
  `Incidents` (list + detail two-pane), `KnowledgeBase` (document cards), `Settings` (a sub-nav
  shell: Integrations — projects master / integration-cards detail — plus AI & usage, Advanced). All three take the shared
  `useDashboard` data plus the top bar's search query and render loading/empty/error states
  explicitly.
- **`src/features/`** — the incident and document workflows (lists, detail, ingest modals).
- **`Dockerfile` / `nginx.conf`** — multi-stage build (Node build → nginx serve) for the Docker Compose
  path above; not used by `npm run dev`. **Caching is asymmetric on purpose**: `/assets/` is
  content-hashed so it's `immutable, max-age=1y`, while `index.html` is `no-cache` because it is the
  pointer to those hashes — a stale copy pins the whole app to an old build. It shipped with no
  `Cache-Control` at all, which lets a browser apply heuristic freshness and reuse it without
  asking; a deploy then lands on the server and never reaches the person looking at it, which is
  indistinguishable from "the fix didn't work".

**Design language**: a light SaaS dashboard with a **permanently-dark navigation rail** (the rail tokens
in `src/index.css` are never theme-flipped — it stays dark in both light and dark mode). Indigo accent,
pastel status/severity pills (always color *and* text label, never color alone), soft rounded cards, an
ambient `.plane-aurora` gradient wash behind the content plane. Fonts: Plus Jakarta Sans (display),
Inter (body), JetBrains Mono (data/ids). Tokens are CSS variables in `src/index.css`; components
reference roles (`bg-surface`, `text-ink`, `--sev-critical`, `--rail-*`) rather than raw hex, so theming
swaps in one place. Tailwind 3.4 caveat: opacity modifiers on CSS-var colors (`ring-accent/25`) don't
compile — use an explicit token or an arbitrary value (`ring-[var(--accent-weak)]`) instead. When the
backend adds a field, update `src/lib/types.ts` (and the relevant page/feature) to render it — keep it
in sync with the DTOs.

## Architecture (`backend/ai/analyze_incident.py`)

The pipeline is: **incident context dict → fingerprint (cache check) → build user message → Bedrock
`converse` → parse JSON → cache**. Four pieces carry the design:

- **`SYSTEM_PROMPT`** — the core of Step 0. Enforces anti-hallucination rules: conclude *only* from provided
  data, never invent metrics/services/events, state explicitly when data is insufficient, and connect
  timestamps (anomaly start vs. deploy time) as evidence. Also fixes the output to a strict JSON schema.
  Changes to analysis behavior usually mean editing this prompt, not the code around it.
- **`build_user_message(ctx)`** — assembles the context into a compact prompt. It only emits sections that
  are present (`ecs`, `alb`, `recent_deploy`, `metrics`, `sample_logs`, `runbook`, `alert`), so the *same*
  function handles both infrastructure incidents and non-infrastructure ones (e.g. third-party API cost
  overage). This "only print what's present" design is deliberate — it saves tokens and keeps one code path
  for all incident shapes. Preserve it when adding new context fields.
- **`fingerprint(ctx)`** — the cache key: `service | normalized-error-signature | deploy-version`. The error
  signature is normalized by stripping digits and hex ids (`re.sub`) so repeats of the same error collapse
  to one key. Crucially, a **different deploy version yields a different fingerprint**, forcing re-analysis
  so a stale cache can't misdiagnose a post-deploy incident.
- **`analyze(ctx)`** — cache-first. On hit, returns the stored result tagged `_cache: HIT (0 tokens)`; on
  miss, calls Bedrock, strips markdown fences from the response, `json.loads` it, caches, and tags
  `_cache: MISS`.

The cache (`_CACHE`) is an in-memory dict with a conceptual TTL constant (`CACHE_TTL_SECONDS`). It is
process-local and not actually time-expired in Step 0.

## Incident context shape

Input is a JSON object describing one incident. All fields are optional except `service`; the two files in
`backend/ai/samples/` are the canonical examples of the two supported shapes:

- **Infrastructure** (`infra_oom.json`): `ecs`, `alb`, `metrics`, `sample_logs`, `recent_deploy`.
- **Non-infrastructure** (`apicost_overage.json`): `alert` (human description) plus `metrics` / `sample_logs`.

When extending the input format, update `build_user_message()` to render the new field and keep the samples
in sync as living documentation.

## Git workflow

`main` is protected. Never commit or push directly to `main` — always work on a branch and land changes via
PR. **Before starting any code change, explicitly create a branch named `<type>/<short-kebab-slug>`**
matching the task, using the type that fits:

| Prefix | Use for |
|---|---|
| `feature/<slug>` | new functionality |
| `fix/<slug>` | non-urgent bug fix |
| `hotfix/<slug>` | urgent production fix |
| `chore/<slug>` | config, setup, cleanup — no behavior change |
| `refactor/<slug>` | restructuring, no behavior change |
| `docs/<slug>` | docs only |
| `test/<slug>` | tests only |

This naming is enforced by judgment (mine), not tooling — pick the type and slug from what the task
actually is. `.claude/hooks/auto-branch.sh` is only a safety net: if code gets edited while still on
`main`/`master` (e.g. a branch step was missed), it auto-creates `chore/auto-<timestamp>` so nothing lands
on `main` — that branch should be renamed or merged into the properly-named one, not used as-is.
`.claude/hooks/block-main-push.sh` denies any `git push` targeting `main`/`master` outright. Both hooks are
a backstop; the policy holds even where they can't reach (e.g. manual git outside Claude Code).

**Always pull `main` first.** Before `git checkout -b` or `git push`, pull `main` from origin — if HEAD is
on `main`, pull it directly; if on another branch, pull `main` down and merge/rebase it in before pushing
further work. Skip this only on the user's explicit say-so. `auto-branch.sh` does this itself
(`git pull --ff-only`) before cutting its fallback branch, but that only covers the one case it fires on.

**PRs**: no `Co-Authored-By` trailer on commits/PRs (`attribution.commit`/`attribution.pr` are set to `""`
in `.claude/settings.json` — don't revert this). Keep PR descriptions short: 3-4 sentences on what changed
and why, not a long Summary/Test-plan write-up.

## Integrations (`integrations` table, migration 0019)

Every external system a project is wired to — AWS, New Relic, Azure DevOps, and whatever comes
next — is one row in `integrations`, not a table per provider:

- **`provider` + `config` (JSONB) + `encrypted_secrets` (JSONB)** — provider-specific fields live in
  the JSON, not in dedicated columns. The table this replaced was shaped for AWS and had New Relic
  pushed through the same columns, so a New Relic **account ID** was stored in a column named
  `region`. Values in `encrypted_secrets` are individually encrypted (keys are plain names like
  `pat`, `api_key`, `secret_access_key`); nothing decrypts outside `CredentialResolver`/adapters.
- **`capabilities` (text[])** — `alarms`, `logs`, `tickets`, later `cost`. This is what lets one AWS
  credential serve several jobs while a New Relic one only supplies alarms.
- **`integration_health`** — one row per (integration, capability), because a broken cost sync says
  nothing about whether alarm polling still works.

**Ports are split by capability, not by vendor** (`domain/integrations/ports.py`): `AlarmSource`
and `TicketSink` today, `LogSource`/`CostSource` as they land. The Settings **Test** button probes
per capability in declaration order — list alarms for a monitoring source, read the destination
project back for a tracker — so a tickets-only provider is exercised for real rather than reported
as "no test available" (which is what it did until 2026-09-13, leaving Azure DevOps PATs untested).
A test never writes: filing a work item to check a PAT would litter the tracker. `infrastructure/integrations/registry.py` is the one
place that declares which provider offers which capability and how to build its adapter — the
poller asks it for an alarm source and holds no vendor names of its own, so **adding a provider is
a registry entry plus an adapter**, touching no existing call site. A capability a provider
advertises but hasn't implemented (AWS `cost`) raises when asked for, rather than quietly
returning nothing.

**AWS has three auth types**, and the difference between them is what they need from the machine:

| `auth_type` | needs on the host | renews itself | for |
|---|---|---|---|
| `sso_oidc` | nothing | yes, until the SSO session policy expires | real deployments, fresh machines |
| `sso` | `~/.aws` mounted `:ro`, kept signed in from a terminal | no | the laptop this was built on |
| `access_key` | nothing | never expires | service accounts, CI |

The sign-in can be narrowed with **"only these accounts"**, applied server-side in the poll
response — filtering in the browser would leave every account in the organisation sitting in the
network tab, which is not what the person asking for it means.

`sso_oidc` runs the same device-authorization flow `aws sso login` runs internally: RegisterClient
→ StartDeviceAuthorization → (operator approves in a browser) → CreateToken → ListAccounts/Roles →
GetRoleCredentials. The first three calls are unsigned, which is what lets a container with nothing
configured start it. Only the **refresh token** is persisted; the credentials it mints last about an
hour and are never stored, and the chosen `account_id`/`role_name` are written into the config
precisely because the refresh token could otherwise reach every role its owner can. In-flight
sign-ins live in memory (`SsoConnections`) — they last minutes, and that assumes one backend
process, which is how this deploys.

The older `sso` type is why sessions kept expiring: the `~/.aws` mount is read-only, so the
container can read a token someone obtained outside but can never renew one.

Adapters take `Integration` and read their own `config`/`encrypted_secrets` keys — those key names
are declared next to the adapter in the registry (`required_config`, `secret_names`) so the
Settings form and the adapter can't drift apart.

One HTTP surface serves every provider: `GET/POST /api/integrations`, `PATCH/DELETE
/api/integrations/{id}`, `/{id}/test|pause|resume|poll`, `/poll`, `/poll-schedule`, plus
`GET /api/providers` — the registry's catalog, which the Settings form builds its fields from, so
**adding a provider needs no frontend change**. The old `/api/cloud-connections` and
`/api/ado-connections` controllers, their use cases, entities, repositories and DTOs are gone; the
legacy tables remain only as migration 0019's rollback path.

Secrets are write-only across that surface: `IntegrationOut` returns `secret_names` (which
credentials exist) and never a value, and omitting a secret on `PATCH` keeps the stored one — so
editing a region never demands a PAT nobody has to hand.

The legacy `cloud_connections` / `ado_connections` tables still exist but **nothing reads or writes
them** — they are the rollback path for migration 0019 and get dropped in a later migration.

## Auth: local passwords + Microsoft Entra ID

Two ways in, one session. Both `POST /api/auth/login` (username/password) and `POST /api/auth/entra`
end by issuing **this application's own JWT**, so everything downstream (`require_role`,
`get_current_user`) is unchanged and never learns how the user signed in. Entra answers *who you
are*; the `admin`/`sre`/`consultant` roles in this database still answer *what you may do*.

- **No client secret.** The frontend is a SPA using Authorization Code + PKCE; `ENTRA_TENANT_ID`
  and `ENTRA_CLIENT_ID` are public identifiers (the browser sends them to Microsoft), and both
  blank disables SSO — the Microsoft button then never renders and the route 501s.
- **What makes the ID token trustworthy** is in `infrastructure/security/entra.py`: signature
  against the tenant's JWKS, `aud` == our client id (otherwise a validly-signed token meant for a
  *different* app would be accepted), `iss`/`tid` == our tenant (single-tenant app), plus expiry.
  Don't relax any of these.
- **First sign-in provisions a Guest — no group, no permissions, no projects.** Everyone in the
  company directory can authenticate — that's what SSO means — so provisioning with any actual
  permission would hand the whole tenant access. An admin assigns a group from the Users page, and
  a later sign-in never touches it (an assignment must survive).
- Accounts are keyed on Entra's `oid` (`users.external_id`), not username/email, which Entra lets
  people change. Their `password_hash` is NULL, which is what structurally keeps them off the
  password login path.
- The Azure app registration needs one redirect URI, platform **Single-page application**:
  `http://localhost:5173/auth/callback`. That path is a **second Vite entry point**
  (`auth-callback.html` → `src/auth-callback.ts`), served ahead of the SPA fallback by `nginx.conf`.

  It exists because **MSAL v5 changed how a popup returns its result**: the opener no longer polls
  the popup's URL, so the redirect page must parse the response itself and broadcast it back over a
  BroadcastChannel — `broadcastResponseToMainFrame()` from `@azure/msal-browser/redirect-bridge`.
  Two wrong shapes for this page each fail silently in their own way, and both were shipped:
  letting the SPA fallback serve it boots the whole app inside MSAL's popup, showing a second
  sign-in screen whose button throws `block_nested_popups`; a static page that does nothing (right
  for MSAL v3) leaves `loginPopup` waiting forever with **no request ever reaching the backend** and
  no error anywhere. If sign-in hangs, check the backend log for `POST /api/auth/entra` — its
  absence means the browser half never completed, and the popup page prints the failure rather than
  closing.

## Authorization: one group per account (migration 0025)

A **group** carries both halves of the answer: a permission level (admin / sre / consultant) *and*
the projects it opens up. A user belongs to exactly one; belonging to none is **Guest** — no
permissions, no projects, the state every new account and every first Entra sign-in starts in.

This replaced a `users.role` column plus a separate teams table. Two controls on one screen, both
of which read as "which group are you in", was the source of every confusing layout attempt: no
arrangement could show which one answered what. `users.role` is therefore **not stored** — it is
`group.role`, read through a joined relationship (`UserRow.group`, `lazy="joined"`, so the auth
query on every request already carries the role *and* the projects). They cannot drift because
there is only one of them.

- **`require_role(...)`** on routes reads that derived role. No route lists `guest`, so a guest
  gets 403 everywhere and the frontend shows the "ask an admin" screen.
- **An admin group is unrestricted**, so the API drops any project list submitted for one rather
  than storing a limit nothing enforces.
- **Lockout guards** (`ManageGroups`) cover every route to the same disaster: moving the last admin
  out of an admin group, making them a Guest, demoting the group's role, or deleting the group.
  All raise `LastAdminError`. They count *members with the admin role*, so an empty second admin
  group isn't mistaken for a safety net.
- **The admin UI is one screen and one column**: a select per user on the Users page
  (`PUT /api/users/{id}/group`, "Guest — no access" being a real option in the list), with the
  resulting projects spelled out underneath as derived text. A Groups modal edits what a group
  *is*; it never edits membership. Keep that split.

`ProjectScope` (`domain/users/scope.py`) carries the answer for one request, built by
`get_project_scope` straight from `current_user.projects` — no second query. Its central
invariant: `names is None` means "no filter" (admin only) and `names == ()` means "a filter that
matches nothing" — reading an empty scope as "no filter" would silently turn every new account
into a superuser, so never collapse the two.

Enforcement is in the **queries**, not the UI: `repo.list(projects=scope.names)`,
`repo.rollup(projects=...)`, `documents.list(projects=...)`. Per-incident routes go through
`_visible_incident()` in `interface/http/incidents.py`, which 404s (not 403s — a 403 confirms the
incident exists) for reads *and* writes alike. Documents with no `service` are shared knowledge and
stay visible to everyone.

The daily report is the one exception to filtering, on purpose: digests are cached per
`(date, service)`, so a scoped user's filtered digest served under the all-projects key would be
handed to admins as if complete. Access is checked on the request instead — a scoped user must name
a project they can see, and only an admin may ask for the all-projects digest.

`ManageUsers` keeps the other guard nothing else can recover from: a **password can't be set on a
non-local account** (an Entra user with a local password has a second way in that bypasses SSO, MFA
and conditional access). It lives in the use case, not the HTTP layer — it's a rule about the
system's state, not about one request.

## Roadmap (planned, not yet implemented)

- **Step 4**: replace the in-memory `_CACHE` with **DynamoDB + TTL**.
- **Serverless / AWS CDK** deployment (`.serverless/`, `cdk.out/` in `.gitignore`).
- `infra/` was renamed to **`iac/`** — it will hold Terraform once infrastructure-as-code work starts
  (not written yet, so the folder is currently empty). `docker-compose.yml` moved to the repo root,
  alongside `backend/` and `frontend/`, so `docker compose up` needs no `-f` flag.
- **Auth** — the frontend `Login` page is UI-only today, no backend auth wired up yet.
- ~~AWS SSO connect-in-app~~ — **built** (`infrastructure/cloud/aws_sso.py`,
  `application/integrations/sso_connect.py`, `features/settings/AwsSsoConnect.tsx`). See the AWS
  auth-type table under Integrations.

The full Phase-1 design (problem/goals, architecture + ADRs, DynamoDB data model, build plan, open
questions) is written up in `.claude/specs/`: `SPEC.md`, `ARCHITECTURE.md`, `DATA_MODEL.md`, `PLAN.md`,
`OPEN_QUESTIONS.md`. Read these before implementing any of the steps above — they're the source of truth
for what to build next, not this section.
