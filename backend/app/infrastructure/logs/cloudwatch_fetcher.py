"""CloudWatch Logs Insights adapter — implements the domain `LogFetcher` port.

Runs a Logs Insights query against one log group for a time window and returns matching lines
(most recent first). The blocking boto3 calls are offloaded with `asyncio.to_thread`, same pattern
as `BedrockAnalyzer`, so they don't block the request event loop.

boto3 CloudWatch Logs Insights: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/logs.html
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import boto3

from app.domain.incidents.entities import LogEvent
from app.infrastructure.config import Settings

_POLL_INTERVAL_SECONDS = 1.0
_MAX_POLLS = 30
_DEFAULT_QUERY = (
    "fields @timestamp, @message"
    " | filter @message like /ERROR|FATAL|Exception|Traceback|5\\d\\d/"
    " | sort @timestamp desc | limit 100"
)


class CloudWatchLogFetcher:
    """LogFetcher backed by CloudWatch Logs Insights (`logs:StartQuery`/`GetQueryResults`)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client("logs", region_name=self._settings.aws_region)
        return self._client

    async def fetch_logs(
        self,
        log_group: str,
        start: datetime,
        end: datetime,
        filter_pattern: str | None = None,
    ) -> list[LogEvent]:
        return await asyncio.to_thread(self._fetch_sync, log_group, start, end, filter_pattern)

    def _fetch_sync(
        self,
        log_group: str,
        start: datetime,
        end: datetime,
        filter_pattern: str | None,
    ) -> list[LogEvent]:
        client = self._get_client()
        query = (
            f"fields @timestamp, @message | filter @message like /{filter_pattern}/"
            " | sort @timestamp desc | limit 100"
            if filter_pattern
            else _DEFAULT_QUERY
        )
        query_id = client.start_query(
            logGroupName=log_group,
            startTime=int(start.timestamp()),
            endTime=int(end.timestamp()),
            queryString=query,
        )["queryId"]

        result = {"status": "Scheduled"}
        for _ in range(_MAX_POLLS):
            result = client.get_query_results(queryId=query_id)
            if result["status"] in ("Complete", "Failed", "Cancelled"):
                break
            time.sleep(_POLL_INTERVAL_SECONDS)

        events: list[LogEvent] = []
        for record in result.get("results", []):
            fields = {f["field"]: f["value"] for f in record}
            message = fields.get("@message", "")
            raw_ts = fields.get("@timestamp")
            timestamp = _parse_insights_timestamp(raw_ts) if raw_ts else start
            events.append(LogEvent(timestamp=timestamp, message=message))
        return events


def _parse_insights_timestamp(raw: str) -> datetime:
    """CloudWatch Logs Insights returns `@timestamp` as `YYYY-MM-DD HH:MM:SS.mmm` in UTC."""
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
