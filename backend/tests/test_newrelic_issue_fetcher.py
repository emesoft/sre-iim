"""Unit tests for NewRelicIssueFetcher. No network — monkeypatches httpx, same convention as
test_ado_client.py.
"""

import uuid

import pytest
from cryptography.fernet import Fernet

from app.domain.integrations.entities import Integration
from app.infrastructure.cloud.newrelic_issues import NewRelicIssueFetcher
from app.infrastructure.security.encryptor import Encryptor

pytestmark = pytest.mark.asyncio

_KEY = Fernet.generate_key().decode()


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _FakeAsyncClient:
    def __init__(self, calls, response):
        self._calls = calls
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, json, headers):
        self._calls.append((url, json, headers))
        return self._response


def _connection(*, account_id: str = "12345", api_key: str | None = "nr-api-key") -> Integration:
    encryptor = Encryptor(_KEY)
    return Integration(
        id=uuid.uuid4(),
        project="gcm",
        env="prod",
        provider="newrelic",
        config={"account_id": account_id} if account_id is not None else {},
        encrypted_secrets={"api_key": encryptor.encrypt(api_key)} if api_key else {},
    )


def _issues_response(issues: list[dict]):
    return _FakeResponse(
        {"data": {"actor": {"account": {"aiIssues": {"issues": {"issues": issues}}}}}}
    )


async def test_critical_open_issue_maps_to_alarm(monkeypatch):
    calls = []
    response = _issues_response(
        [
            {
                "issueId": "abc-123",
                "title": ["CRITICAL - SES Errors"],
                "priority": "CRITICAL",
                "state": "ACTIVATED",
                "conditionName": "CRITICAL - SES Errors",
                "policyName": "GCM-SES-Alerts",
            }
        ]
    )
    monkeypatch.setattr(
        "app.infrastructure.cloud.newrelic_issues.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls, response),
    )
    fetcher = NewRelicIssueFetcher(Encryptor(_KEY))
    alarms = await fetcher.list_alarms(_connection())

    assert len(alarms) == 1
    assert alarms[0].arn == "abc-123"
    assert alarms[0].name == "CRITICAL - SES Errors"
    assert alarms[0].state == "ALARM"
    assert alarms[0].reason == "GCM-SES-Alerts"
    # title == conditionName here, so there's nothing extra to say — detail stays unset rather
    # than repeating the same short phrase back as its own "detail".
    assert alarms[0].detail is None
    # Auth uses New Relic's own header, not a bearer token.
    assert calls[0][2]["API-Key"] == "nr-api-key"
    variables = calls[0][1]["variables"]
    assert variables["accountId"] == 12345
    # A window is always sent: New Relic defaults to about a day, which hid a live open
    # issue that had been firing for two (see tests/test_newrelic_window.py).
    assert variables["end"] - variables["start"] >= 7 * 24 * 3600 * 1000
    assert variables["cursor"] is None


async def test_closed_issue_maps_to_ok(monkeypatch):
    calls = []
    response = _issues_response(
        [
            {
                "issueId": "abc-123",
                "title": "CRITICAL - SES Errors",
                "priority": "CRITICAL",
                "state": "CLOSED",
            }
        ]
    )
    monkeypatch.setattr(
        "app.infrastructure.cloud.newrelic_issues.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls, response),
    )
    fetcher = NewRelicIssueFetcher(Encryptor(_KEY))
    alarms = await fetcher.list_alarms(_connection())
    assert alarms[0].state == "OK"


async def test_fuller_title_is_carried_as_detail_when_it_differs_from_the_name(monkeypatch):
    calls = []
    response = _issues_response(
        [
            {
                "issueId": "abc-123",
                "title": ["Log query result is > 0.0 on 'CRITICAL - Yelp Errors'"],
                "priority": "CRITICAL",
                "state": "ACTIVATED",
                "conditionName": "CRITICAL - Yelp Errors",
                "policyName": "GCM-Yelp-Alerts",
            }
        ]
    )
    monkeypatch.setattr(
        "app.infrastructure.cloud.newrelic_issues.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls, response),
    )
    fetcher = NewRelicIssueFetcher(Encryptor(_KEY))
    alarms = await fetcher.list_alarms(_connection())
    assert alarms[0].name == "CRITICAL - Yelp Errors"
    assert alarms[0].detail == "Log query result is > 0.0 on 'CRITICAL - Yelp Errors'"


async def test_non_critical_issues_are_filtered_out(monkeypatch):
    calls = []
    response = _issues_response(
        [
            {"issueId": "1", "title": "warn", "priority": "HIGH", "state": "ACTIVATED"},
            {"issueId": "2", "title": "crit", "priority": "CRITICAL", "state": "ACTIVATED"},
        ]
    )
    monkeypatch.setattr(
        "app.infrastructure.cloud.newrelic_issues.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls, response),
    )
    fetcher = NewRelicIssueFetcher(Encryptor(_KEY))
    alarms = await fetcher.list_alarms(_connection())
    assert [a.arn for a in alarms] == ["2"]


async def test_graphql_errors_raise(monkeypatch):
    calls = []
    response = _FakeResponse({"errors": [{"message": "invalid account id"}]})
    monkeypatch.setattr(
        "app.infrastructure.cloud.newrelic_issues.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls, response),
    )
    fetcher = NewRelicIssueFetcher(Encryptor(_KEY))
    with pytest.raises(RuntimeError, match="invalid account id"):
        await fetcher.list_alarms(_connection())


async def test_non_numeric_account_id_raises_a_clear_error():
    fetcher = NewRelicIssueFetcher(Encryptor(_KEY))
    with pytest.raises(ValueError, match="account_id"):
        await fetcher.list_alarms(_connection(account_id="not-a-number"))


async def test_policy_name_as_a_list_is_flattened_to_a_string(monkeypatch):
    """Regression: NerdGraph's aiIssues.policyName is `[String]`, not `String` — treating it as a
    plain string embedded a Python list repr like "['GCM-SES-Alerts']" into the alert text."""
    calls = []
    response = _issues_response(
        [
            {
                "issueId": "abc-123",
                "title": ["Log query result is > 0.0 on 'CRITICAL - SES Errors'"],
                "priority": "CRITICAL",
                "state": "ACTIVATED",
                "conditionName": "CRITICAL - SES Errors",
                "policyName": ["GCM-SES-Alerts"],
            }
        ]
    )
    monkeypatch.setattr(
        "app.infrastructure.cloud.newrelic_issues.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls, response),
    )
    fetcher = NewRelicIssueFetcher(Encryptor(_KEY))
    alarms = await fetcher.list_alarms(_connection())
    assert alarms[0].reason == "GCM-SES-Alerts"
    # conditionName (short, no embedded quotes) is preferred over the full templated title, which
    # can itself contain a quoted condition name and would break headline extraction downstream.
    assert alarms[0].name == "CRITICAL - SES Errors"


async def test_condition_name_as_a_list_is_also_flattened(monkeypatch):
    """Regression found against a live New Relic account: conditionName ALSO comes back as
    `[String]`, not the plain `String` originally assumed. Left unfixed, the raw list value
    (`['CRITICAL - SES Errors']`) hit `tracked_alarms.alarm_name` (a VARCHAR column) and crashed
    the whole poll with an asyncpg DataError — not just an ugly string."""
    calls = []
    response = _issues_response(
        [
            {
                "issueId": "abc-123",
                "title": ["Log query result is > 0.0 on 'CRITICAL - SES Errors'"],
                "priority": "CRITICAL",
                "state": "ACTIVATED",
                "conditionName": ["CRITICAL - SES Errors"],
                "policyName": ["GCM-SES-Alerts"],
            }
        ]
    )
    monkeypatch.setattr(
        "app.infrastructure.cloud.newrelic_issues.httpx.AsyncClient",
        lambda **kw: _FakeAsyncClient(calls, response),
    )
    fetcher = NewRelicIssueFetcher(Encryptor(_KEY))
    alarms = await fetcher.list_alarms(_connection())
    assert isinstance(alarms[0].name, str)
    assert alarms[0].name == "CRITICAL - SES Errors"
    # metric_name is set straight from conditionName too, and must be flattened the same way —
    # this is what actually reaches context.metrics.metric_name in the created incident.
    assert isinstance(alarms[0].metric_name, str)
    assert alarms[0].metric_name == "CRITICAL - SES Errors"


async def test_missing_api_key_raises():
    fetcher = NewRelicIssueFetcher(Encryptor(_KEY))
    with pytest.raises(ValueError, match="API key"):
        await fetcher.list_alarms(_connection(api_key=None))
