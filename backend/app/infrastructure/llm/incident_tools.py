"""The investigation tools, in one place, in a form both chat backends can use.

Chat originally ran only through the Claude Code CLI, which reaches these over MCP. Serving them
directly through the Anthropic API needs the same four functions described a second way — as
Messages-API tool schemas — and two copies of "what does this tool take and do" would drift the
moment one gained an argument.

Every tool is read-only and scoped to one incident's project. `service` is fixed at construction,
never taken from the model, so no tool call can be steered into another project's account.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime

from app.infrastructure.cloud.aws_enricher import AwsLookups
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.config import Settings
from app.infrastructure.db.repositories.integrations import SqlAlchemyIntegrationRepository
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.logs.factory import resolve_log_fetcher
from app.infrastructure.security.encryptor import Encryptor

#: Anthropic Messages-API tool definitions. The descriptions are prompt text — they are the only
#: thing telling the model when reaching for a tool beats guessing, so they say *why* as well as
#: what.
TOOL_SCHEMAS: list[dict] = [
    {
        "name": "describe_alarm",
        "description": (
            "Look up a CloudWatch alarm's definition and current state: threshold, how missing "
            "data is treated, the dimensions it watches, and CloudWatch's own reason for the "
            "current state. Start here — the reason usually names the actual condition, and the "
            "dimensions tell you which cluster/service/instance the alarm is about. Never infer "
            "that from the alarm's name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"alarm_name": {"type": "string"}},
            "required": ["alarm_name"],
        },
    },
    {
        "name": "metric_datapoints",
        "description": (
            "Read the real datapoints behind a metric: average, maximum, latest, and how many "
            "there were. `dimensions` comes from describe_alarm. An empty result is itself the "
            "answer — a metric reporting nothing means something very different from one "
            "reporting a low number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "metric_name": {"type": "string"},
                "dimensions": {"type": "object", "additionalProperties": {"type": "string"}},
                "minutes": {"type": "integer", "default": 60},
            },
            "required": ["namespace", "metric_name", "dimensions"],
        },
    },
    {
        "name": "ecs_service_state",
        "description": (
            "Desired vs running vs pending task count for an ECS service, why its recent tasks "
            "stopped, and its latest service events. This is what separates a deliberate "
            "scale-to-zero from a crash loop — the two readings of 'no tasks are reporting'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"cluster": {"type": "string"}, "service": {"type": "string"}},
            "required": ["cluster", "service"],
        },
    },
    {
        "name": "fetch_logs",
        "description": (
            "Fetch real log lines for this incident's service. `start`/`end` are ISO-8601 "
            "timestamps; `filter_pattern` is an optional CloudWatch Logs Insights regex — omit it "
            "for the default error filter."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "log_group": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "filter_pattern": {"type": "string"},
            },
            "required": ["log_group", "start", "end"],
        },
    },
]

TOOL_NAMES = tuple(t["name"] for t in TOOL_SCHEMAS)


@dataclass
class IncidentTools:
    """Runs one tool call for one project. Never raises: a failed lookup is returned as text for
    the model to reason about, because an exception here would end the whole chat turn over a
    missing IAM permission."""

    service: str
    settings: Settings

    async def run(self, name: str, args: dict) -> str:
        try:
            if name == "describe_alarm":
                return await self._aws(lambda lk: lk.alarm(args["alarm_name"]))
            if name == "metric_datapoints":
                return await self._aws(
                    lambda lk: lk.metrics(
                        args["namespace"],
                        args["metric_name"],
                        args.get("dimensions") or {},
                        int(args.get("minutes") or 60),
                    )
                )
            if name == "ecs_service_state":
                return await self._aws(lambda lk: lk.ecs(args["cluster"], args["service"]))
            if name == "fetch_logs":
                return await self._logs(args)
        except Exception as exc:  # noqa: BLE001 - reported to the model, never raised at the caller
            return f"{name} failed: {exc}"
        return f"unknown tool: {name}"

    async def _aws(self, call) -> str:
        lookups = await self._lookups()
        if lookups is None:
            return (
                f"No AWS integration is configured for project {self.service!r}, so I can't query "
                "it. An admin can add one on the Settings page."
            )
        result = await asyncio.to_thread(call, lookups)
        return json.dumps(result, default=str) if result else "(nothing found)"

    async def _lookups(self) -> AwsLookups | None:
        """Resolved per call from `service`, which is fixed at construction — a tool argument could
        otherwise name another project and read an account this incident has no claim to."""
        async with SessionLocal() as session:
            integration = await SqlAlchemyIntegrationRepository(session).for_provider(
                self.service, "aws"
            )
        if integration is None:
            return None
        # Off-thread: resolving an `sso_oidc` integration refreshes the token and mints role
        # credentials over the network, and the resolver refuses to do that on the event loop.
        session = await asyncio.to_thread(
            CredentialResolver(Encryptor(self.settings.secret_encryption_key)).resolve, integration
        )
        return AwsLookups(session=session, region=(integration.config or {}).get("region"))

    async def _logs(self, args: dict) -> str:
        fetcher = await resolve_log_fetcher(self.service, self.settings)
        events = await fetcher.fetch_logs(
            args["log_group"],
            datetime.fromisoformat(args["start"]),
            datetime.fromisoformat(args["end"]),
            args.get("filter_pattern"),
        )
        if not events:
            return "(no matching log lines in this window)"
        return "\n".join(
            f"{e.timestamp.isoformat()} {e.level or '-'} {e.message}" for e in events
        )
