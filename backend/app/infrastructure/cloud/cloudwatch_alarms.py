"""Reads CloudWatch alarm states for one connection via boto3 (sync SDK, run in a thread —
same pattern as infrastructure/llm/chat.py's BedrockChatModel)."""

from __future__ import annotations

import asyncio
import re

from app.domain.integrations.entities import AlarmState, Integration
from app.infrastructure.cloud.credential_resolver import CredentialResolver

# CloudWatch has no priority field — an alarm is just in ALARM or not. Teams encode urgency in the
# alarm name instead, so that's the only signal available. Matching is deliberately strict (a
# whole word, one of these markers) rather than a fuzzy guess: the priority gates spending an LLM
# call automatically, so a name that says nothing stays `None` — "the provider never told us this
# was urgent" — rather than being talked into a severity it never claimed.
_NAME_PRIORITY = (
    ("critical", re.compile(r"\b(critical|sev-?1|p1)\b", re.IGNORECASE)),
    ("high", re.compile(r"\b(high|sev-?2|p2|urgent)\b", re.IGNORECASE)),
)


def priority_from_name(name: str) -> str | None:
    for priority, pattern in _NAME_PRIORITY:
        if pattern.search(name):
            return priority
    return None


class CloudWatchAlarmFetcher:
    def __init__(self, resolver: CredentialResolver) -> None:
        self._resolver = resolver

    async def list_alarms(self, integration: Integration) -> list[AlarmState]:
        def _fetch() -> list[AlarmState]:
            session = self._resolver.resolve(integration)
            client = session.client("cloudwatch", region_name=integration.config.get("region"))
            alarms: list[AlarmState] = []
            for page in client.get_paginator("describe_alarms").paginate():
                for a in page.get("MetricAlarms", []):
                    alarms.append(
                        AlarmState(
                            arn=a["AlarmArn"],
                            name=a["AlarmName"],
                            state=a["StateValue"],
                            reason=a.get("StateReason"),
                            metric_name=a.get("MetricName"),
                            namespace=a.get("Namespace"),
                            priority=priority_from_name(a["AlarmName"]),
                        )
                    )
            return alarms

        return await asyncio.to_thread(_fetch)
