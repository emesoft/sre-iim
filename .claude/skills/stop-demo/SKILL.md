---
name: stop-demo
description: Use when finished with the local IIM stack and reclaiming machine resources — "tắt hết đi", "dọn sạch cho nhẹ máy", "off và xóa hết", "stop the demo", "clean up docker". Covers stopping containers, wiping demo data, removing built images, and quitting Docker Desktop.
---

# Stop and clean up the demo stack

## Overview

Four levels of teardown. They are not interchangeable: level 1 keeps everything, level 3 destroys
the database. Pick from what the user actually wants back — disk, RAM, or a clean slate.

**`-v` is irreversible.** It deletes every incident, knowledge document, and cached analysis. If the
user seeded knowledge docs for a demo, they re-seed them by hand afterward.

## Pick the level

| User says | Level | Command | Keeps |
|---|---|---|---|
| "tạm dừng", "pause", back to it shortly | 1 | `docker compose stop` | everything; `docker compose start` resumes |
| "tắt đi", done for now | 2 | `docker compose down` | **data** (volume survives); images stay |
| "xóa hết", "reset", demo it again from scratch | 3 | `docker compose down -v` | images only |
| "cho nhẹ máy", reclaiming disk | 4 | `docker compose down -v --rmi local` | nothing |

Ambiguous ask → **ask which**, naming the consequence: "level 3 wipes the incidents and knowledge
docs you seeded — that what you want?" Guessing wrong here costs the user their demo data.

Level 4 also frees ~1–2 GB of images; rebuilding later takes a few minutes.

## Steps

Run from the repo root (that is where `docker-compose.yml` lives).

```bash
docker compose down -v          # adjust to the chosen level
```

`Ctrl+C` on a foreground `up` only stops containers — it does not remove them. Still run the command.

### Verify it is actually gone

```bash
docker compose ps -a                     # no rows
docker volume ls | grep llm-sre          # gone after -v
docker images | grep llm-sre             # gone after --rmi local
```

### Then free the RAM

```bash
osascript -e 'quit app "Docker"'         # macOS; Docker Desktop holds a VM open
```

Worth mentioning to the user: Docker Desktop is usually the biggest thing still resident after the
containers are gone.

## Never do this

**Do not run `docker system prune -a`.** It deletes images, build cache, and networks belonging to
every other project on the machine, not just this one. `docker compose down --rmi local` removes
exactly what this stack built. If the user explicitly asks for a machine-wide prune, confirm they
understand it hits their other projects first.

Do not `rm -rf` any Postgres data by hand — the data lives in the named volume `llm-sre_pgdata`, and
`down -v` is the supported way to remove it.

## Related

- `.env` holds live API keys. If the user is done with the project entirely, suggest revoking the
  OpenRouter and Jina keys — deleting `.env` alone does not invalidate them.
- Bringing it back up: the `start-demo` skill.
