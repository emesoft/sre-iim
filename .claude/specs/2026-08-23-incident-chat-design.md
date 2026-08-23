# Incident Chat (Claude, tool-calling) — Design

Status: approved, pending implementation plan
Date: 2026-08-23

## Problem

The incident detail page's "Search logs" panel (`LogSearchPanel` → `POST
/api/incidents/{id}/logs/search`) requires the user to manually pick a log group and a time
window, then re-runs the whole 5-field analysis against the fetched lines. This is rigid: the user
can't ask a free-form follow-up ("check logs of that service", "was there a deploy around that
time?") and get a direct, conversational answer grounded in the incident.

Replace it with a chat panel: the user asks Claude questions about the incident, and when Claude
doesn't have enough information from the incident's own context, it decides on its own to pull more
data (starting with CloudWatch logs) rather than the user having to drive a separate form.

## Goals

- A chat panel on the incident detail page, scoped to that one incident, seeded with its full raw
  context (same JSON currently shown in the "Raw context" `<details>` panel).
- Claude can autonomously call a `fetch_logs` tool mid-conversation when it decides it needs log
  lines it doesn't already have — the user just asks in plain language; no separate form.
- Multi-turn: follow-up messages carry the prior conversation's context without the client
  re-sending full history every time.
- Chat history persists per incident (survives a page reload).

## Non-goals (explicitly out of scope for this iteration)

- A `get_metric_data` tool (e.g. "DB connections count at that time") — no CloudWatch metric-fetch
  code exists anywhere in this app yet (today's log search is the only "pull more real data"
  capability). Ship `fetch_logs` first; add metric tools as a separate follow-up once this shape is
  proven.
- Streaming the assistant's reply token-by-token — `POST .../chat` is a single blocking call, same
  UX pattern as the existing (now-removed) log-search endpoint. A "thinking…" spinner covers the
  wait; SSE streaming can be a later enhancement if replies feel too slow.
- Bedrock/DeepSeek chat — this only works for `LLM_PROVIDER=claude_cli`, since it depends on the
  real Claude Code CLI's own MCP/tool-use loop (see "Why claude_cli only" below). When a different
  provider is configured, the chat endpoint returns a clear 501, not a silent no-op.
- Editing/deleting past chat messages, regenerating a reply, or exporting the transcript.

## Why claude_cli only, and why an MCP server (not a hand-rolled tool loop)

Anthropic's Messages API (`POST /v1/messages` with a `tools` array) is the normal way to give a
model real tool-calling — but that endpoint is billed per token against an API key, not usable
against a Claude Code subscription's OAuth token. This app deliberately uses `claude -p` headless
(`app/infrastructure/llm/claude_cli.py`) specifically to run on a Claude Pro/Max **subscription**
instead of a paid API key (see that file's module docstring). To get real tool-calling under that
same subscription auth, the tool has to be something **Claude Code CLI itself** invokes during its
own agentic loop — which means an MCP server passed via `--mcp-config`, not a loop we write and
drive ourselves against the Messages API.

This is why every other analysis path in this app (`ClaudeCliAnalyzer`, `ClaudeCliChatModel`) runs
`claude -p` with `--tools ""` (no tools at all) — until now, nothing needed Claude to act
autonomously mid-call. Incident chat is the first feature that does.

## Architecture

```
Frontend ChatPanel
  → POST /api/incidents/{id}/chat {message}
    → IncidentChat use case (application layer)
      → look up or create ChatSession(incident_id) → claude_session_id
      → write MCP config JSON to a temp file:
          {"mcpServers": {"iim-tools": {"command": "python3",
            "args": ["-m", "app.infrastructure.llm.mcp_log_tool"],
            "env": {"IIM_INCIDENT_SERVICE": incident.service}}}}
      → run `claude -p <message>
              --mcp-config <tmp file> --strict-mcp-config
              --allowedTools "mcp__iim-tools__fetch_logs"
              --append-system-prompt <incident context + chat rules>
              --session-id <uuid>   (first turn)
              --resume <uuid>       (later turns)
              --output-format json --safe-mode --model <model>`
        ... Claude Code's own agentic loop decides whether to call fetch_logs,
            possibly more than once, before producing its final text.
      → parse `result` text + `usage` (same shape as ClaudeCliAnalyzer already parses)
      → persist both the user message and the assistant reply to `chat_messages`
      → return the assistant reply
```

The MCP server (`mcp_log_tool.py`) is a **separate short-lived process** Claude Code spawns per
chat turn (stdio transport) — it is not our FastAPI process. It runs in the same container image,
so it can import this app's own code directly rather than calling back over HTTP:

```python
# app/infrastructure/llm/mcp_log_tool.py (new, stdio MCP server, entry point via `python3 -m`)
from mcp.server.fastmcp import FastMCP
from app.infrastructure.config import get_settings
from app.infrastructure.logs.factory import build_log_fetcher

mcp = FastMCP("iim-tools")
_SERVICE = os.environ["IIM_INCIDENT_SERVICE"]  # baked in at launch — Claude can't ask for another project's logs

@mcp.tool()
async def fetch_logs(log_group: str, start: str, end: str, filter_pattern: str | None = None) -> str:
    """Fetch recent log lines from a CloudWatch (or demo) log group for THIS incident's service."""
    fetcher = build_log_fetcher(_SERVICE, get_settings())
    events = await fetcher.fetch_logs(log_group, datetime.fromisoformat(start), datetime.fromisoformat(end), filter_pattern)
    return "\n".join(f"{e.timestamp.isoformat()} {e.level or ''} {e.message}" for e in events) or "(no matching log lines)"

mcp.run(transport="stdio")
```

Reusing `build_log_fetcher(service, settings)` — the exact factory the old `LogSearchPanel` used —
means: CloudWatch or `DEMO_LOGS=true` both keep working unchanged, and the tool is scoped to one
project's logs by construction (the service is baked into the subprocess's env at launch, not a
parameter Claude controls), so a chat about an `rxdevs` incident cannot be steered into fetching
`EVP` logs.

New dependency: `mcp` (official Python MCP SDK, PyPI) added to `backend/pyproject.toml`.

## Data model (new tables)

**`chat_sessions`** — one row per incident that has ever been chatted with; holds the Claude Code
session id so later turns can `--resume` it instead of re-sending history.

| column | type | notes |
|---|---|---|
| `incident_id` | uuid pk, fk → incidents | one chat thread per incident |
| `claude_session_id` | uuid | passed to `--session-id` (first turn) / `--resume` (later turns) |
| `created_at` | timestamptz | |

**`chat_messages`** — persisted transcript, rendered by the frontend on load.

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `incident_id` | uuid, fk → incidents, index | |
| `role` | text | `user` \| `assistant` |
| `content` | text | |
| `input_tokens` | int, nullable | assistant messages only — same "None = untracked, cache HIT = 0" convention as `analyses.input_tokens` |
| `output_tokens` | int, nullable | |
| `created_at` | timestamptz | |

## Domain / application layer

- `ChatMessage` entity (`domain/incidents/entities.py` or a new `domain/chat/entities.py` —
  decide at plan time based on whether chat grows enough to warrant its own module).
- `ChatRepository` port: `get_or_create_session(incident_id) -> ChatSession`,
  `add_message(...)`, `list_messages(incident_id) -> list[ChatMessage]`.
- `IncidentChat` use case (application layer): the orchestration in the Architecture diagram above.
  Takes the incident, the user's message, resolves/creates the session, builds the MCP config +
  system prompt (incident context serialized the same way `build_user_message()` already does),
  invokes a new `ClaudeCliChat.send(session_id, message, mcp_config, allowed_tools)` method in
  `infrastructure/llm/claude_cli.py`, persists both messages, returns the assistant `ChatMessage`.

## API surface (new)

- `GET /api/incidents/{id}/chat` — list persisted messages, oldest first.
- `POST /api/incidents/{id}/chat` `{message: string}` → the new assistant `ChatMessage` (id, role,
  content, input_tokens, output_tokens, created_at). Blocking call (see Non-goals).
- Both 501 with a clear message when `LLM_PROVIDER != "claude_cli"`.

## Frontend

- New `features/incidents/ChatPanel.tsx`, replacing `<LogSearchPanel>` in `IncidentDetail.tsx`.
- Simple message list (user bubbles right-aligned, assistant left-aligned, matching the app's
  existing pastel/rounded card language) + a text input + send button.
- Loads history via `GET .../chat` on mount; appends optimistically on send, replaces with the
  real response (or shows an inline error) when the POST resolves; a "Claude is thinking…" state
  covers the blocking wait.
- The "Raw context" `<details>` panel stays (per the earlier decision to keep it) — the chat is an
  additional way to interrogate the incident, not a replacement for seeing the ground truth.

## Security / safety

- `--strict-mcp-config` ensures only the one `iim-tools` MCP server we generate is available —
  no project-level or user-level MCP config on the host/container leaks into the chat session.
- `--allowedTools "mcp__iim-tools__fetch_logs"` is an explicit allowlist — Claude cannot invoke any
  other tool (no Bash, no file access) during an incident chat, same "safe-mode, no unrelated
  context" posture as the existing analysis calls.
- `fetch_logs` is read-only (wraps the existing `LogFetcher.fetch_logs`, itself read-only) and
  scoped to the one incident's `service` at subprocess-launch time, not model-controlled input —
  consistent with this project's standing "cloud providers are read-only by default" rule.

## Known limitations (accepted for this iteration)

- No metric-fetching tool yet (see Non-goals) — chat can only pull more logs, not e.g. DB connection
  counts, until that tool is built separately.
- Blocking request/response, no live "Claude is calling fetch_logs now…" progress — the user just
  sees a spinner for however long the turn takes (analysis + any tool calls).
- One chat thread per incident, no branching/regeneration.
- `--resume` ties conversation continuity to the Claude Code CLI's own local session storage inside
  the backend container's filesystem — if the container is recreated (not just restarted) between
  turns, `--resume` will fail to find that session id; the use case should treat that failure as
  "start a fresh session and try again once" rather than a hard error, so a redeployed container
  doesn't permanently break a given incident's chat.
