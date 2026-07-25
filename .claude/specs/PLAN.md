# IIM — Build Plan / Backlog (Phase 1)

- **Date:** 2026-07-17 · **Status:** Draft (design only — this is the backlog for later phases). Steps
  2–4 below describe a Lambda/EventBridge auto-collector that was never built; T2.1's log search and
  T4.2's ADO ticketing landed instead as **on-demand actions in the Postgres/FastAPI app** (SRE-triggered
  from the incident UI, not alarm-triggered) — see
  [2026-07-25-sre-workflow-log-search-known-issue-ticketing-report.md](./2026-07-25-sre-workflow-log-search-known-issue-ticketing-report.md).
- **Related:** [SPEC.md](./SPEC.md) · [ARCHITECTURE.md](./ARCHITECTURE.md) · [DATA_MODEL.md](./DATA_MODEL.md) · [OPEN_QUESTIONS.md](./OPEN_QUESTIONS.md)

**Walking-skeleton strategy:** build a thin end-to-end path first with everything faked, then replace each
fake part with a real one. Every step leaves something runnable.

Legend: **DW** = "done when". Dependencies reference task ids.

---

## Step 0 — AI layer ✅ DONE

Already implemented: `backend/ai/analyze_incident.py` + `backend/ai/samples/*.json`. No AWS needed.
- **DW (met):** a sample context in → convincing 5-field JSON out, grounded only in the provided data.

---

## Step 1 — Fake skeleton (thin end-to-end, hardcoded context)

**Goal:** event → hardcoded context → AI → DynamoDB → board displays it. Test locally.

| Task | Description | DW | Deps |
|---|---|---|---|
| T1.1 | Define `IncidentContext` (TypedDict) + `Collector` Protocol (interface only) | Types import cleanly; typecheck passes | — |
| T1.2 | Analyzer Lambda handler: event → **hardcoded** `IncidentContext` → `ai.analyze()` → write `iim-incidents` | Local invoke writes an incident record containing the AI analysis | T1.1 |
| T1.3 | IaC for `iim-incidents` (+ GSI `by_service`) and `iim-cache` (+ TTL) | Tables provision (or local DynamoDB) and T1.2 writes to them | — |
| T1.4 | Read Lambda + API Gateway: `GET /incidents`, `GET /incidents/{id}` | Endpoints return JSON from DynamoDB | T1.3 |
| T1.5 | React board (3-column single pane) reading the API | Board displays the AI analysis for the hardcoded incident | T1.4 |

- **DW (step):** running an event by hand → the board shows the AI analysis (context still fake).

---

## Step 2 — Real collector

**Goal:** replace the hardcoded context with real, read-only AWS data for `GCM`.

| Task | Description | DW | Deps |
|---|---|---|---|
| T2.1 | Implement `AWSCollector.collect_context`: ECS `Describe*`, ELBv2 target health, CloudWatch `GetMetricData`, Logs Insights (filter on `level`), recent deploy | Returns a valid `IncidentContext` for `GCM` | T1.1 |
| T2.2 | Wire the Analyzer Lambda to use `AWSCollector` instead of the hardcoded context | Lambda analyzes real gathered context | T1.2, T2.1 |
| T2.3 | Config mapping: trigger event → service + log group (`GCM`) | Event resolves to the correct service/log group | T2.1 |
| T2.4 | Read-only IAM policy for the Analyzer Lambda | Lambda runs with only `Describe*`/`Get*`/`logs:StartQuery`+`GetQueryResults` (+ table + secret) | T2.1 |

- **DW (step):** the Lambda gathers real context from `GCM` → the board shows analysis of real data.

---

## Step 3 — Real trigger + sandbox

**Goal:** a self-built sandbox that can be broken on demand, wired to the full pipeline.

| Task | Description | DW | Deps |
|---|---|---|---|
| T3.1 | Build sandbox ECS (Fargate) service emitting **JSON logs with `level`**, plus a "break it" control (OOM and 5xx) | Pressing the control produces OOM/5xx and matching logs | — |
| T3.2 | CloudWatch metric alarm (ALB 5xx / ECS memory) + Logs **metric filter** alarm (`FATAL`/`OOMKilled`) → EventBridge → Analyzer Lambda | An alarm invokes the Analyzer Lambda automatically | T2.2, T3.1 |
| T3.3 | (Optional) SNS email notification off the alarm | On-call receives an email (does not block the pipeline) | T3.2 |
| T3.4 | AWS **budget alert** | Budget alert active before continuous running | — |

- **DW (step):** pressing the sandbox button → the whole pipeline runs → the board shows the incident.

---

## Step 4 — Fill in the rest (cache, ticketing, polish)

| Task | Description | DW | Deps |
|---|---|---|---|
| T4.1 | Cache-first via `iim-cache` + DynamoDB TTL: Lambda looks up fingerprint before calling the LLM; on-read TTL check | A recurring incident returns from cache (visible speed difference), no new LLM call | T1.3, T3.2 |
| T4.2 | One-way Azure DevOps ticket (Services, **Bug**), PAT in Secrets Manager, dedup by fingerprint (comment on recurrence), gated on AI `severity=critical` | Serious incidents auto-create one Bug; recurrences comment, no duplicates | T3.2, ADO creds (OPEN_QUESTIONS) |
| T4.3 | Polish the 3-column board; deploy static build to S3 (+ CloudFront if HTTPS needed) | Board looks clean and loads from S3 | T1.5, T3.2 |
| T4.4 | Optional model escalation flag (Haiku→Sonnet on low-confidence critical), off by default | Flag exists and works when enabled; default path uses Haiku only | T2.2 |

- **DW (step):** recurring incident returns from cache; tickets auto-create without duplicates; board clean on S3.

---

## Step 5 — Freeze & demo

| Task | Description | DW | Deps |
|---|---|---|---|
| T5.1 | Freeze features | No new scope after this point | Step 4 |
| T5.2 | Measure MTTR before/after; compute monthly cost | Numbers captured for the slide (target ~15 min → <1 min) | Step 4 |
| T5.3 | Write demo script + prepare a fallback demo (recorded / cached data) | Script rehearsed; fallback ready if live demo fails | T5.1 |

- **DW (step):** MTTR and cost measured, demo script ready, fallback prepared.

---

## Dependency summary

```
Step 0 ✅ → Step 1 → Step 2 → Step 3 → Step 4 → Step 5
                T1.1 ─────────► T2.1
                T1.3 ─────────────────────► T4.1
                T3.2 ─────────────────────► T4.1, T4.2, T4.3
```

**Critical open dependency:** T4.2 needs Azure DevOps org/project + PAT (see OPEN_QUESTIONS). Everything
through Step 3 can proceed without it.
