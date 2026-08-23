"""MCP stdio server exposing `fetch_logs` to the incident-chat Claude Code CLI session (design
spec .claude/specs/2026-08-23-incident-chat-design.md).

Spawned by `claude -p --mcp-config ...` as a short-lived subprocess per chat turn — this is NOT
part of the FastAPI process. It runs in the same container image, so it imports this app's own
log-fetching code directly (`build_log_fetcher`) instead of calling back over HTTP.

`IIM_INCIDENT_SERVICE` (set in the MCP config's `env`, see `ClaudeCliChat._run_turn` in
`claude_cli.py`) scopes every call to one incident's project. It is read from the environment, not
a tool parameter, so Claude cannot ask this tool to fetch a different project's logs.
"""

from __future__ import annotations

import os
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from app.infrastructure.config import get_settings
from app.infrastructure.logs.factory import build_log_fetcher

mcp = FastMCP("iim-tools")
_SERVICE = os.environ["IIM_INCIDENT_SERVICE"]


@mcp.tool()
async def fetch_logs(
    log_group: str, start: str, end: str, filter_pattern: str | None = None
) -> str:
    """Fetch recent log lines for this incident's service from its CloudWatch (or demo) log
    group. `start`/`end` are ISO-8601 timestamps. `filter_pattern` is an optional CloudWatch Logs
    Insights `like` regex; omit it to use the default error-pattern filter."""
    try:
        fetcher = build_log_fetcher(_SERVICE, get_settings())
        events = await fetcher.fetch_logs(
            log_group,
            datetime.fromisoformat(start),
            datetime.fromisoformat(end),
            filter_pattern,
        )
    except Exception as exc:  # noqa: BLE001 - report the failure back to Claude as tool output, don't crash the MCP server
        return f"fetch_logs failed: {exc}"
    if not events:
        return "(no matching log lines in this window)"
    return "\n".join(f"{e.timestamp.isoformat()} {e.level or '-'} {e.message}" for e in events)


if __name__ == "__main__":
    mcp.run(transport="stdio")
