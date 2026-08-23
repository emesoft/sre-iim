# Project Registry — Design

Status: approved, pending implementation plan
Date: 2026-08-23

## Problem

`CloudConnection.project` and `AdoConnection.project` are each free-text strings, populated in the
frontend from two independently hardcoded `KNOWN_PROJECTS` arrays (`CloudConnectionForm.tsx`,
`AdoConnectionForm.tsx`) plus an "Other..." option that lets the user type anything. Nothing ties
these two fields together: a typo in one form (e.g. `rxdev` vs `rxdevs`) silently creates a
connection for a project that doesn't match any other connection, and there's no way to discover
"what projects exist" other than reading whatever strings happen to already be in the two tables.

This surfaced directly: after adding the per-project Azure DevOps feature, configuring an ADO
connection for `rxdevs` required re-typing the project name from scratch in a second form, with no
guard against a mismatch against the CloudConnection already configured for the same project.

## Goals

- One shared, authoritative list of project names that both `CloudConnectionForm` and
  `AdoConnectionForm` (and any future per-project connection type — e.g. Jira) select from, instead
  of each form maintaining its own hardcoded list plus free-text entry.
- Adding a connection can no longer reference a project that doesn't exist in the registry — this is
  enforced by the database, not just discipline in the frontend forms.
- Deleting a project is blocked while any connection still references it.

## Non-goals (explicitly out of scope)

- `incidents.service` does **not** get any FK/registry constraint. Incidents can arrive for any
  service via a CloudWatch alarm firing on a project that hasn't been (or will never be) registered
  here — ingest must stay permissive. The registry only governs the two *connection* tables.
- No restructuring of the Settings page layout. The existing "AWS connections" and "Azure DevOps
  connections" tables stay exactly where they are; this only adds a small "Projects" list above them
  and changes the `project` field in both forms from free text to a dropdown sourced from that list.
- No `project_id` foreign-key columns. See "Why a text FK, not an ID FK" below.
- No cascading UI (e.g. a project detail page showing its connections nested underneath). That's the
  "group by project" layout alternative that was explicitly declined during design.
- No rename capability in this iteration — `ProjectRegistry.tsx` only supports add/delete, and there
  is no `PATCH /api/projects/{id}`. The FK is still declared `ON UPDATE CASCADE` (a rename done
  directly at the database level would safely propagate), but nothing in the app surfaces a way to
  trigger that rename. Add a rename endpoint + UI as a follow-up if it's ever needed.

## Why a text FK, not an ID FK

The obvious "textbook" approach is a `project_id UUID` column on both connection tables, replacing
the text `project` column, with API responses joining back to the project's name. That was rejected
for this iteration: `project` is read as a plain string in several places that would all need
updating to resolve through a join — most notably
`AdoConnectionRepository.get_by_project(incident.service)`, called from the ticket-creation endpoint
with the incident's own `service` string, plus `ManageCloudConnections`/`ManageAdoConnections` and
their tests. Project names change rarely, so the ID indirection buys little today.

Instead, `projects.name` is `UNIQUE`, and `cloud_connections.project` /
`ado_connections.project` each get a database-level
`FOREIGN KEY (project) REFERENCES projects(name) ON UPDATE CASCADE ON DELETE RESTRICT`.
This gets the same integrity guarantee (can't set a `project` value that isn't in the registry) and
the same rename-propagation (`ON UPDATE CASCADE`) as an ID-based FK, without touching any of the
existing string-based lookups — every current `.project` / `get_by_project(name)` call site keeps
working unchanged.

## Architecture

```
projects table (id, name UNIQUE, created_at)
        ▲                              ▲
        │ FK project→name              │ FK project→name
        │ ON UPDATE CASCADE            │ ON UPDATE CASCADE
        │ ON DELETE RESTRICT           │ ON DELETE RESTRICT
        │                              │
cloud_connections.project      ado_connections.project
```

New domain module `app/domain/projects/`:
- `entities.py` — `Project(id: uuid.UUID | None, name: str, created_at: datetime | None)`.
- `ports.py` — `ProjectRepository` Protocol: `add(project) -> Project`, `list() -> list[Project]`,
  `get(id) -> Project | None`, `delete(id) -> None`. No `update`/rename method — see "Non-goals".

New persistence:
- Migration `0013_projects.py` (`down_revision = "0012_ado_connections"`):
  1. Create `projects` table: `id UUID PK`, `name TEXT NOT NULL UNIQUE`, `created_at`.
  2. Backfill: `INSERT INTO projects (id, name, created_at) SELECT gen_random_uuid(), name, now()
     FROM (SELECT DISTINCT project AS name FROM cloud_connections UNION SELECT DISTINCT project FROM
     ado_connections) AS distinct_projects` — every project name already in use by either table gets
     a registry row, so the FK step below never fails on existing data.
  3. Add the FK constraints described above to `cloud_connections.project` and
     `ado_connections.project`.
  - `downgrade()` drops both FK constraints, then drops the `projects` table.
- `ProjectRow` in `orm.py`, `SqlAlchemyProjectRepository` in
  `infrastructure/db/repositories/projects.py` (mirrors `SqlAlchemyAdoConnectionRepository`'s shape:
  `add`/`list`/`get`/`delete`).
- A `delete()` that hits the `ON DELETE RESTRICT` constraint raises the underlying
  `IntegrityError` — the application layer (`ManageProjects.delete`) catches it and re-raises a
  domain-level error the HTTP layer maps to `409 Conflict` with a message naming the constraint
  (`"project is still referenced by one or more connections"`).

New application use case: `app/application/projects/manage.py` — `ManageProjects(projects, uow)`
with `create(name) -> Project`, `list() -> list[Project]`, `delete(id) -> None` (catches the FK
violation as described above).

New HTTP surface (admin-gated, mirrors `ado_connections.py`):
- `GET /api/projects` — list, `200`.
- `POST /api/projects` — body `{name: str}`, `201`, `422` if `name` is blank, `409` if a project
  with that name already exists (unique-constraint violation).
- `DELETE /api/projects/{id}` — `204`, `409` if referenced by any connection, `404` if unknown.

DTOs: `ProjectOut(id, name, created_at)`, `ProjectCreateRequest(name: str)`.

### Existing connection-creation paths must handle the new FK cleanly

Once the FK constraints exist, `ManageCloudConnections.create` and `ManageAdoConnections.create` can
receive a `project` value that doesn't exist in the registry (a stale frontend, a direct API call) and
the insert will raise an `IntegrityError` neither currently catches. Left uncaught, this is the same
class of bug just fixed live in `poll_alarms.py` earlier today (an unhandled DB error 500ing the whole
request instead of a clean, expected-case response). Both `create` methods must catch the
FK-violation `IntegrityError` and re-raise a domain-level error (reusing `ManageProjects`'s pattern)
that their HTTP routes map to `422` with a message like
`"unknown project '<name>' — add it in the Projects registry first"`.

Both forms also let a user change the `project` field while editing an existing connection, which
goes through `ManageCloudConnections.update`/`ManageAdoConnections.update` instead of `create` — the
same FK violation is reachable there too, so both `update` methods get the identical catch-and-map
treatment as their `create` counterparts.

## Frontend

- `src/lib/types.ts` — `Project { id, name, created_at }`, `ProjectCreate { name }`.
- New `src/features/settings/ProjectRegistry.tsx` — a small card: text input + "Add" button, and a
  list of existing project names each with a "Delete" button (disabled with a tooltip-style inline
  message when the delete call comes back `409`, showing the backend's error text via the existing
  `errText` helper). Placed at the top of `SettingsContent`'s returned JSX, above `ClaudeTokenForm`.
- `Settings.tsx` — add `projects` state, a `loadProjects()` call (`GET /api/projects`) alongside the
  existing `load()`/`loadSchedule()`/`loadAdo()` in the mount `useEffect`, and pass `projects` down to
  both connection forms.
- `CloudConnectionForm.tsx` / `AdoConnectionForm.tsx` — replace only the **Project** field's
  `SelectOrOtherField` (hardcoded `KNOWN_PROJECTS` + "Other..." free-text branch) with a plain
  `<select>` bound to a new `projects: Project[]` prop
  (`{projects.map(p => <option value={p.name}>{p.name}</option>)}`). If `projects` is empty, render
  a short inline message ("No projects yet — add one above") instead of an empty dropdown, so the
  form doesn't silently submit an empty string. `CloudConnectionForm.tsx` keeps `SelectOrOtherField`
  for its Env and Region fields — those aren't part of this registry — so the component itself is
  not touched or removed, only the "Project" field's markup changes in both forms.

## Testing

- `test_manage_projects.py` — `create` happy path, `create` duplicate name raises, `delete` happy
  path, `delete` of a referenced project raises the domain error.
- `test_projects_http.py` — mirrors `test_ado_connections_http.py`'s fixture shape: create+list,
  create-duplicate→409, create-blank→422, delete-unreferenced→204, delete-referenced→409 (create a
  `CloudConnection` or `AdoConnection` against the project first).
- `test_migration_0013` is not a separate test file (this repo doesn't unit-test migrations
  directly) — verified manually against the running dev database as part of implementation, the same
  way `0011_chat`'s patch and `0012_ado_connections` were verified earlier in this project.
- Existing `test_cloud_connections_http.py` / `test_ado_connections_http.py` fixtures that create a
  connection with a `project` value must insert a matching `projects` row first (or the FK now
  rejects the insert) — this is a required update to those two test files, not new tests.

## Migration note for the FK backfill

Because `cloud_connections`/`ado_connections` already have real rows in the dev database (`EVP`,
`rxdevs`, ...), the backfill step (2) above must run before the FK step (3) in the same migration —
if the FK constraint were added first, adding it would fail immediately against the existing
un-backed rows. The migration's `upgrade()` function performs both steps in that order, in one
transaction, so there's no window where the constraint exists without the backing rows.
