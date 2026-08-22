"""Reads CloudWatch alarm states for one connection via boto3 (sync SDK, run in a thread —
same pattern as infrastructure/llm/chat.py's BedrockChatModel)."""

from __future__ import annotations

import asyncio

from app.domain.cloud_connections.entities import AlarmState, CloudConnection
from app.infrastructure.cloud.credential_resolver import CredentialResolver


class CloudWatchAlarmFetcher:
    def __init__(self, resolver: CredentialResolver) -> None:
        self._resolver = resolver

    async def list_alarms(self, connection: CloudConnection) -> list[AlarmState]:
        def _fetch() -> list[AlarmState]:
            session = self._resolver.resolve(connection)
            client = session.client("cloudwatch", region_name=connection.region)
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
                        )
                    )
            return alarms

        return await asyncio.to_thread(_fetch)
