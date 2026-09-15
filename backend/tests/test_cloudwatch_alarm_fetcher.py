"""Unit tests for CloudWatchAlarmFetcher — fakes the boto3 CloudWatch client, no real AWS call."""

import pytest

from app.domain.integrations.entities import Integration
from app.infrastructure.cloud.cloudwatch_alarms import CloudWatchAlarmFetcher

pytestmark = pytest.mark.asyncio

_CONNECTION = Integration(
    project="GCM", env="prod", provider="aws",
    config={"region": "ap-southeast-1", "auth_type": "sso", "sso_profile_name": "p"},
)


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self):
        return iter(self._pages)


class _FakeClient:
    def __init__(self, pages):
        self._pages = pages

    def get_paginator(self, name):
        assert name == "describe_alarms"
        return _FakePaginator(self._pages)


class _FakeSession:
    def __init__(self, pages):
        self._pages = pages

    def client(self, service_name, region_name=None):
        assert service_name == "cloudwatch"
        return _FakeClient(self._pages)


class _FakeResolver:
    def __init__(self, session):
        self._session = session

    def resolve(self, connection):
        return self._session


async def test_lists_alarms_across_pages():
    pages = [
        {"MetricAlarms": [{"AlarmArn": "arn:1", "AlarmName": "cpu-high", "StateValue": "ALARM",
                           "StateReason": "high cpu", "MetricName": "CPUUtilization", "Namespace": "AWS/ECS"}]},
        {"MetricAlarms": [{"AlarmArn": "arn:2", "AlarmName": "mem-high", "StateValue": "OK"}]},
    ]
    fetcher = CloudWatchAlarmFetcher(_FakeResolver(_FakeSession(pages)))
    alarms = await fetcher.list_alarms(_CONNECTION)
    assert [a.arn for a in alarms] == ["arn:1", "arn:2"]
    assert alarms[0].state == "ALARM"
    assert alarms[0].reason == "high cpu"
    assert alarms[1].state == "OK"
    assert alarms[1].reason is None
