"""MCP stdio server exposing this app's investigation tools to the incident-chat Claude Code CLI
session (design spec .claude/specs/2026-08-23-incident-chat-design.md).

Spawned by `claude -p --mcp-config ...` as a short-lived subprocess per chat turn — this is NOT
part of the FastAPI process. It runs in the same container image, so it imports this app's own
log-fetching code directly (`resolve_log_fetcher`) instead of calling back over HTTP.

`IIM_INCIDENT_SERVICE` (set in the MCP config's `env`, see `ClaudeCliChat._run_turn` in
`claude_cli.py`) scopes every call to one incident's project. It is read from the environment, not
a tool parameter, so Claude cannot ask these tools for a different project's data.

Beyond logs, the AWS tools here answer the questions chat could previously only *recommend* that
somebody go and run. Without them a chat turn ended in a block of `aws ecs describe-services`
commands and an offer to interpret the output if the reader pasted it back — a checklist, when the
app already holds the credentials needed to just look. Every tool is read-only.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from app.infrastructure.cloud.aws_enricher import AwsLookups
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.config import get_settings
from app.infrastructure.db.repositories.integrations import SqlAlchemyIntegrationRepository
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.logs.factory import resolve_log_fetcher
from app.infrastructure.security.encryptor import Encryptor

mcp = FastMCP("iim-tools")
_SERVICE = os.environ["IIM_INCIDENT_SERVICE"]


async def _lookups() -> AwsLookups | None:
    """AWS access for this incident's project only.

    The integration is looked up by `_SERVICE` from the environment rather than by anything Claude
    passes, so the tools below cannot be talked into reading another project's account. Returns
    None when the project has no AWS integration — the tool then says so plainly instead of failing
    in a way that reads like the resource doesn't exist.
    """
    async with SessionLocal() as session:
        # By provider, not by capability: a project whose alarms come from New Relic can still
        # have an AWS account attached, and that account is what these tools need.
        integration = await SqlAlchemyIntegrationRepository(session).for_provider(_SERVICE, "aws")
    if integration is None:
        return None
    encryptor = Encryptor(get_settings().secret_encryption_key)
    # Off-thread: resolving an `sso_oidc` integration refreshes the token and mints role
    # credentials over the network, and the resolver refuses to do that on the event loop.
    session = await asyncio.to_thread(CredentialResolver(encryptor).resolve, integration)
    return AwsLookups(session=session, region=(integration.config or {}).get("region"))


@mcp.tool()
async def fetch_logs(
    log_group: str, start: str, end: str, filter_pattern: str | None = None
) -> str:
    """Fetch recent log lines for this incident's service from its CloudWatch (or demo) log
    group. `start`/`end` are ISO-8601 timestamps. `filter_pattern` is an optional CloudWatch Logs
    Insights `like` regex; omit it to use the default error-pattern filter."""
    try:
        fetcher = await resolve_log_fetcher(_SERVICE, get_settings())
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


def _no_aws() -> str:
    return (
        f"No AWS integration is configured for project {_SERVICE!r}, so I can't query it. "
        "An admin can add one on the Settings page."
    )


@mcp.tool()
async def describe_alarm(alarm_name: str) -> str:
    """Look up a CloudWatch alarm's definition and current state: threshold, how missing data is
    treated, the dimensions it watches, and CloudWatch's own reason for the current state. Use this
    first — the reason usually names the actual condition, and the dimensions tell you which
    cluster/service/instance the alarm is about (never guess that from the alarm's name)."""
    lookups = await _lookups()
    if lookups is None:
        return _no_aws()
    try:
        found = await asyncio.to_thread(lookups.alarm, alarm_name)
    except Exception as exc:  # noqa: BLE001 - report back to Claude, don't kill the MCP server
        return f"describe_alarm failed: {exc}"
    return json.dumps(found, default=str) if found else f"no alarm named {alarm_name!r}"


@mcp.tool()
async def metric_datapoints(
    namespace: str, metric_name: str, dimensions: dict[str, str], minutes: int = 60
) -> str:
    """Read the real datapoints behind a metric (average, maximum, latest, and how many there
    were). `dimensions` comes from `describe_alarm`. An empty result is itself the answer: a metric
    reporting nothing means something very different from one reporting a low number."""
    lookups = await _lookups()
    if lookups is None:
        return _no_aws()
    try:
        data = await asyncio.to_thread(
            lookups.metrics, namespace, metric_name, dimensions, minutes
        )
    except Exception as exc:  # noqa: BLE001
        return f"metric_datapoints failed: {exc}"
    return json.dumps(data, default=str)


@mcp.tool()
async def ecs_service_state(cluster: str, service: str) -> str:
    """Desired vs running vs pending task count for an ECS service, why its recent tasks stopped,
    and its latest service events. This is what separates a deliberate scale-to-zero from a crash
    loop — the two readings of "no tasks are reporting metrics"."""
    lookups = await _lookups()
    if lookups is None:
        return _no_aws()
    try:
        state = await asyncio.to_thread(lookups.ecs, cluster, service)
    except Exception as exc:  # noqa: BLE001
        return f"ecs_service_state failed: {exc}"
    return json.dumps(state, default=str) if state else f"no ECS service {service!r} in {cluster!r}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
