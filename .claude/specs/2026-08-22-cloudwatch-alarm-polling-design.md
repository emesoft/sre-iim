# CloudWatch Alarm Polling — Design

Status: approved, pending implementation plan
Date: 2026-08-22

## Problem

IIM currently only learns about incidents when something calls `POST /api/incidents` by hand (or via
the demo log source). For real SRE use, incidents should surface automatically from CloudWatch Alarms:
when an alarm goes into `ALARM` state, an incident should appear and be analyzed without anyone
triggering it manually; when the alarm recovers, the incident should close itself.

This also requires a way to store how to connect to each AWS account IIM needs to watch — some accounts
use IAM Identity Center (SSO) profiles already configured on the host (`~/.aws/config`, see the `ss-*` /
`evp-*` profiles), others don't have SSO enabled and need a plain access key/secret.

## Goals

- Poll CloudWatch Alarms across one or more configured AWS accounts on a schedule (demo-friendly: **1
  hour**, not 5 minutes — keeps API calls and cost low while still showing the mechanism working).
- Auto-create an incident when an alarm enters `ALARM`; auto-resolve it when the alarm returns to `OK`.
- Support both credential styles per connection: an SSO profile name (reads the host's mounted
  `~/.aws`) or an access key/secret pair (encrypted at rest in Postgres).
- A manual "Refresh" action that runs the same poll immediately, for demo purposes (no waiting an hour
  to show it works).

## Non-goals (explicitly out of scope for this iteration)

- Alarm filtering by tag/prefix — v1 polls every alarm in the connected account.
- Any cloud besides AWS (`cloud` field is already generic in the connection model for future Azure/GCP,
  but only AWS is implemented).
- Deduplicating/suppressing flapping alarms — an alarm that flips `ALARM`→`OK`→`ALARM` repeatedly will
  open and close incidents repeatedly. Accepted risk for v1; flagged as a known limitation.
- KMS/Secrets Manager — access keys are encrypted with an application-level key (see below), not a
  managed secret store.

## Data model (new tables)

**`cloud_connections`**

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `project` | text, no DB constraint | free text; the Settings UI offers the known monorepo divisions (BEC/EVP/GCM/SmartSuite, plus `IIM` itself) as a dropdown with an "other" free-entry option, so new divisions don't need a migration |
| `env` | text | free text, e.g. `dev`, `staging`, `prod` |
| `cloud` | text | `aws` only for now |
| `region` | text | e.g. `ap-southeast-1` |
| `auth_type` | text | `sso` \| `access_key` |
| `sso_profile_name` | text, nullable | required when `auth_type=sso`; must exist in the host's mounted `~/.aws/config` |
| `encrypted_access_key_id` | text, nullable | required when `auth_type=access_key`; Fernet-encrypted |
| `encrypted_secret_access_key` | text, nullable | required when `auth_type=access_key`; Fernet-encrypted |
| `last_poll_at` | timestamptz, nullable | |
| `last_poll_status` | text, nullable | `ok` \| `error` |
| `last_poll_error` | text, nullable | last error message, shown in the Settings UI |
| `created_at` | timestamptz | |

**`tracked_alarms`** — the state IIM remembers between polls so it can tell an `OK`→`ALARM` transition
from an alarm that was already open, and know which incident to auto-resolve on `ALARM`→`OK`.

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `connection_id` | uuid fk → `cloud_connections` | |
| `alarm_arn` | text | unique per connection |
| `alarm_name` | text | |
| `last_state` | text | `OK` \| `ALARM` |
| `incident_id` | uuid fk → incidents, nullable | set while an incident from this alarm is open |
| `updated_at` | timestamptz | |

## Credentials & security

- **Encryption**: a `Fernet` key from a new `SECRET_ENCRYPTION_KEY` env var (never stored in the DB)
  encrypts `encrypted_access_key_id` / `encrypted_secret_access_key` before insert, decrypts them only
  in memory at the moment a boto3 session is built.
- **`CredentialResolver`** (new infra component): given a `CloudConnection`, returns a `boto3.Session`:
  - `auth_type=sso` → `boto3.Session(profile_name=connection.sso_profile_name)` — relies on the host's
    `~/.aws` already being mounted read-only into the backend container (already wired in
    `docker-compose.yml` for the per-project CloudWatch log search feature; this reuses that mount).
  - `auth_type=access_key` → `boto3.Session(aws_access_key_id=..., aws_secret_access_key=...,
    region_name=connection.region)` with the decrypted values.
- **API never returns secrets**: `GET /api/cloud-connections` responses mask
  `encrypted_access_key_id`/`encrypted_secret_access_key` as `****`; the plaintext only ever exists
  transiently in the resolver.
- **Test connection**: `POST /api/cloud-connections/{id}/test` calls `describe_alarms(MaxRecords=1)`
  with the resolved session and reports pass/fail — lets a user validate a new connection (SSO logged
  in, keys correct) without waiting for the next scheduled poll.
- SSO note carried over from earlier discussion: a profile with `sso_registration_scopes =
  sso:account:access` refreshes its access token from a cached refresh token automatically, so the
  background job does not need a human to re-open a browser every poll — only when the refresh token
  itself expires (org-controlled, commonly ~90 days), at which point `last_poll_status=error` surfaces
  it.

## Polling & state machine

- **`PollAlarmsJob`** (new application use case), triggered two ways:
  1. Scheduled: `AsyncIOScheduler` (APScheduler), interval **60 minutes**, started in the FastAPI
     lifespan alongside the existing app startup.
  2. Manual: `POST /api/cloud-connections/poll` (all connections) or `POST
     /api/cloud-connections/{id}/poll` (one) — the Settings/Incidents page "Refresh" button calls this;
     it runs the exact same job code, not a separate path.
- For each active connection: resolve credentials → `describe_alarms()` → for each alarm returned,
  compare to `tracked_alarms`:
  - Not tracked yet, or tracked as `OK`, and CloudWatch now says `ALARM` → create an incident (context
    built in the existing "alert" shape — alarm name, metric, threshold, description, region — the same
    shape `build_user_message()` already renders for non-infrastructure incidents) via the existing
    `IngestIncident` use case; store the new incident's id on the `tracked_alarms` row; set
    `last_state=ALARM`.
  - Tracked as `ALARM` and CloudWatch now says `OK` → call the existing `ResolveIncident` use case on
    the stored `incident_id`; set `last_state=OK`; clear `incident_id`.
  - Tracked as `ALARM` and still `ALARM` → no-op (prevents duplicate incidents for an alarm that's
    still firing).
- **Per-connection error isolation**: a failure resolving credentials or calling AWS for one connection
  (expired SSO token, revoked key, wrong region) is caught, written to
  `last_poll_status`/`last_poll_error` on that connection, and does not stop the job from polling the
  remaining connections.

## API surface (new)

- `POST /api/cloud-connections` — create
- `GET /api/cloud-connections` — list (secrets masked)
- `PATCH /api/cloud-connections/{id}` — update
- `DELETE /api/cloud-connections/{id}` — delete
- `POST /api/cloud-connections/{id}/test` — test credentials, returns pass/fail
- `POST /api/cloud-connections/poll` and `POST /api/cloud-connections/{id}/poll` — run `PollAlarmsJob`
  now (Refresh button)

## Frontend

- New **Settings** page: a form to add a connection (project select from the known divisions + free
  env text; auth type toggle between SSO profile name and access key/secret inputs; region), a table of
  existing connections showing `last_poll_status`/`last_poll_at` as a badge, and per-row Test / Delete
  actions.
- A **Refresh** button (Settings page, and/or Incidents page) calls the manual poll endpoint and shows a
  toast/spinner while it runs.
- Incidents created from an alarm show a small badge (e.g. "CloudWatch Alarm") in the list/detail so
  they're distinguishable from manually-ingested incidents. Requires a new field on the incident DTO
  (e.g. `source: "manual" | "cloudwatch_alarm"`) — track this through `types.ts` per the existing "keep
  DTOs in sync" convention.

## Known limitations (accepted for this iteration)

- Flapping alarms open/close incidents repeatedly — no debounce/suppression window in v1.
- No alarm filtering (tag/prefix) — every alarm in a connected account is watched.
- Secrets are encrypted with an app-level key, not a managed secret store (KMS/Secrets Manager).
- 1-hour poll interval means a real alarm can take up to an hour to surface automatically outside of a
  manual Refresh.
