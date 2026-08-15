---
name: start-demo
description: Use when bringing the IIM stack up locally to click through it by hand — "chạy app lên", "start the demo", "dựng stack để test", "run it end to end so I can try it". Covers db + backend + frontend together, and the embedding-dimension gotcha when running pytest against the running stack.
---

# Start the demo stack

## Overview

`docker compose up` is one line, but the stack is not usable the moment it returns: migrations run
at backend container start, and the two API keys are what make analysis work at all. This skill is
the preflight → start → **prove it's ready** sequence.

**A container in state `running` is not a working app.** Only `/healthz` returning
`{"status":"ok","app":"IIM","database":"up"}` proves the backend finished migrating and reached
Postgres — the backend reports `Up` for several seconds before that is true.

## Steps

### 1. Preflight — all three, before `up`

```bash
docker info >/dev/null 2>&1 && echo "docker ok" || echo "docker OFF"
grep -n 'PASTE_' .env 2>/dev/null || echo "keys filled"
lsof -i :8000 -i :5173 -i :5432 -sTCP:LISTEN 2>/dev/null || echo "ports free"
```

- **Docker off** → `open -a Docker`, then wait for the daemon (step 2's pattern) before continuing.
- **`.env` missing or still has `PASTE_...`** → **STOP and ask the user for the keys.** Do not start
  anyway: the stack comes up looking healthy, then every incident fails with a 401 and reads as a
  broken app. Keys: OpenRouter (`DEEPSEEK_API_KEY`) + Jina (`JINA_API_KEY`), both required — analysis
  embeds a retrieval query before it calls the LLM, so one key alone is not enough.
- **Port busy** → override in `.env`: `DB_HOST_PORT`, `FRONTEND_HOST_PORT` (backend's 8000 is fixed
  in `docker-compose.yml`).

### 2. Start detached, then wait for readiness

```bash
docker compose up -d --build
```

**Always `-d`.** A foreground `up` blocks the session until the user interrupts it.

Then wait with a **backgrounded** Bash call (`run_in_background: true`) — it exits on its own and
notifies you, so you are not polling. Foreground `sleep` is blocked by the harness; backgrounded is
fine.

```bash
for i in $(seq 1 90); do
  curl -sf localhost:8000/healthz >/dev/null && break
  sleep 2
done
curl -s localhost:8000/healthz || docker compose logs backend --tail=50
```

Bounded on purpose: it ends within ~3 minutes whether the backend came up or is crash-looping, and
prints the logs you need in the failure case instead of hanging. Do not swap in an unbounded
`until` — a crash loop then looks identical to a slow start.

### 3. Verify, then report

```bash
docker compose ps
curl -s localhost:8000/healthz                       # status ok + database up
curl -so /dev/null -w "%{http_code}\n" localhost:5173  # 200 = nginx serving the build
```

Tell the user: **http://localhost:5173**, and that the walkthrough is in `DEMO.md`.

State plainly that a healthy stack proves the app is up — **not** that AI analysis works. That is
only proven once an incident has actually been ingested. Do not claim it before then.

## When it fails

| Symptom | Cause | Fix |
|---|---|---|
| healthz never returns | migrations still running, or backend crash-looping | `docker compose logs backend --tail=50` |
| `connection refused` on 5432 | another Postgres owns the port | set `DB_HOST_PORT` in `.env`, `up -d` again |
| backend exits immediately | bad `DATABASE_URL` or a failed migration | read the logs before retrying |
| stack is up but incidents 401 | keys not reaching the container | `docker compose config \| grep -E 'DEEPSEEK_API_KEY\|JINA_API_KEY'` |
| stack is up but incidents 429 | OpenRouter free-tier limit | switch `DEEPSEEK_MODEL` to another `:free` model, recreate backend |

## Running pytest while the stack is up

The 21 DB-backed tests skip themselves when no Postgres is reachable. Once this stack is running they
execute — but the embedding dimension has to match, or 5 of them fail with
`asyncpg.exceptions.DataError: expected 768 dimensions, not 1024`:

```bash
cd backend && EMBEDDING_DIM=768 uv run pytest    # 102 passed
```

The demo `.env` sets `EMBEDDING_DIM=768` (Jina), so migration 0002 sized the live DB column to 768.
`uv run pytest` alone reads `backend/.env` — which does not exist — and falls back to the 1024
default, so the app under test disagrees with the schema. Pass the dim explicitly; do not "fix" the
tests, and do not change the root `.env` to 1024 without recreating the DB (`docker compose down -v`).

## Red flags

- Running `up` without `-d` — blocks the session.
- Reporting "the app is running" from `docker compose ps` alone.
- Starting with placeholder keys "so the user can see the UI" — hides the failure until demo time.
- Rebuilding with `--build` on every restart when nothing changed; plain `up -d` is faster.
