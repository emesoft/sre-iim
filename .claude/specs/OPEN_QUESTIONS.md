# IIM — Open Questions (Phase 1)

- **Date:** 2026-07-17 · **Status:** Draft
- **Related:** [SPEC.md](./SPEC.md) · [ARCHITECTURE.md](./ARCHITECTURE.md) · [DATA_MODEL.md](./DATA_MODEL.md) · [PLAN.md](./PLAN.md)

Decisions already made during design are recorded at the bottom. The items below still need input.

## Blocking a specific task

### Q1 — Azure DevOps org, project, and PAT secret *(blocks T4.2)*
- **Decided:** Azure DevOps **Services** (dev.azure.com), work-item type **Bug**, PAT in AWS Secrets Manager.
- **Still needed:** the **organization** and **project** names, and the **Secrets Manager secret name/ARN**
  that will hold the PAT. (Do not paste the PAT here or in git.)
- **Why:** the ADO REST endpoint is `https://dev.azure.com/{org}/{project}/_apis/wit/workitems/$Bug`, and the
  Lambda needs the exact secret name for its `secretsmanager:GetSecretValue` grant.
- **Update (2026-07-25):** implemented as an on-demand action (`POST /api/incidents/{id}/ticket`), not a
  Lambda — org/project/PAT are read straight from env (`AZDO_ORG`/`AZDO_PROJECT`/`AZDO_PAT`, no Secrets
  Manager yet since there's no Lambda to grant access to). Still needs real org/project/PAT values to test
  live; see the new design doc linked at the top of `PLAN.md`.

### Q2 — Exact `GCM` ECS service name + log group *(blocks T2.1 / T2.3)*
- **Decided:** the single demo service is **`GCM`**, emitting **JSON logs with a `level` field**.
- **Still needed:** the exact **ECS cluster + service name** and the **CloudWatch log group** the collector
  should query (for the sandbox we build in Step 3, these are the names we choose; if pointing at an
  existing service instead, provide its real names).
- **Why:** `AWSCollector` and the Logs Insights query key off these identifiers.
- **Update (2026-07-25):** the log group is no longer config-resolved — the SRE types it into the incident
  detail page's log-search panel per search (`POST /api/incidents/{id}/logs/search`), since there's no
  auto-collector yet to need a fixed mapping. AWS auth is per-project SSO profiles
  (`PROJECT_<SERVICE>_AWS_PROFILE`), not a single Lambda execution role — see the new design doc.

### Q3 — Confirmed Bedrock model id (APAC inference profile) *(blocks T2.2 runtime)*
- **Decided:** default model **Claude Haiku 4.5**; optional Sonnet 5 escalation.
- **Still needed:** the exact **Bedrock model / inference-profile id** for `ap-southeast-1`, confirmed in the
  Bedrock console → **Model access**. APAC typically requires a regional inference-profile prefix
  (e.g. `apac.anthropic.claude-...`), and Model access must be enabled for the chosen model(s).
- **Why:** the `boto3` Converse `modelId` must be the exact string; a bare model id may fail in this region.

## Non-blocking (has a working default)

### Q4 — Trigger path: keep SNS in the critical path, or EventBridge-only?
- **Default (proceeding with):** `Alarm state-change → EventBridge → Lambda`, with **SNS for human email
  only** (ADR-005). Fewer hops, less IAM surface.
- **Alternative:** the brief's `Alarm → SNS → EventBridge → Lambda` (SNS in the critical path).
- **Impact if changed later:** small — it's a wiring/IaC change, not an application change.

### Q5 — CloudFront in front of the S3 board?
- **Default:** S3 static hosting only. Add CloudFront **only if** HTTPS is required for the demo.
- **Impact:** minor; additive.

### Q6 — Incident history retention
- **Default:** a 7-day housekeeping `ttl` on `iim-incidents` so the prototype table doesn't grow forever.
- **Open:** confirm 7 days is fine for the demo, or drop the TTL entirely for a short-lived prototype.

---

## Decisions already locked (for reference)

| # | Decision |
|---|---|
| D1 | Ticketing target = Azure DevOps **Services**; work-item type = **Bug**; **one-way** create + recurrence comment |
| D2 | Demo service = **`GCM`**; logs are **JSON with a `level` field** |
| D3 | Default model = **Claude Haiku 4.5**; **Sonnet 5** escalation optional, off by default |
| D4 | Data store = **two DynamoDB tables** (`iim-incidents` + GSI `by_service`, `iim-cache` + TTL) |
| D5 | LLM gating: only **CRITICAL-class** alarms fire the pipeline; AI `severity` gates ticket creation |
| D6 | Collector = `Collector` Protocol + `AWSCollector`; `IncidentContext` pinned by `build_user_message()` |
| D7 | Region = `ap-southeast-1`; all artifacts in **English** |
