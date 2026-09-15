"""Unit tests for how much of New Relic we actually ask for — no network.

`aiIssues.issues` applies its own default time window when none is given, and that default is about
a day. Against a live account the unwindowed query returned 4 issues; seven days returned 40. An
issue that activated two days ago and was *still open* — `CRITICAL - SES Errors` — therefore never
reached the poller, and no incident was ever created for it. Nothing failed: a short list looks
exactly like a quiet account.

The same shape of silence applies to paging, so both are pinned here.
"""

import json

from app.infrastructure.cloud.newrelic_issues import _LOOKBACK, _MAX_PAGES, _QUERY


def test_the_query_asks_for_an_explicit_window():
    """Leaving it to New Relic's default is what hid a live, open CRITICAL issue."""
    assert "timeWindow" in _QUERY
    assert "$start" in _QUERY and "$end" in _QUERY


def test_the_window_is_wide_enough_for_a_long_running_issue():
    """A day was demonstrably too short. An alert that has been firing all week is precisely the
    one worth seeing, not the one to age out of the query."""
    assert _LOOKBACK.days >= 7


def test_the_query_follows_a_cursor():
    """A capped page is indistinguishable from a calm account — the alarms past the end simply
    never become incidents."""
    assert "nextCursor" in _QUERY
    assert "$cursor" in _QUERY


def test_paging_is_bounded():
    """A stop, not a limit: an account noisier than this has a different problem, and an unbounded
    loop against a paginating API is how a poll cycle never returns."""
    assert 1 < _MAX_PAGES <= 100


async def test_every_page_is_collected(monkeypatch):
    """Two pages in, both pages' issues out — the failure mode is silently keeping only the first."""
    import httpx

    from app.domain.integrations.entities import Integration
    from app.infrastructure.cloud import newrelic_issues
    from app.infrastructure.security.encryptor import Encryptor

    key = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="
    encryptor = Encryptor(key)
    pages = [
        {"nextCursor": "page-2", "issues": [_issue("1", "First")]},
        {"nextCursor": None, "issues": [_issue("2", "Second")]},
    ]
    seen_cursors: list[str | None] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            seen_cursors.append(json["variables"]["cursor"])
            page = pages[len(seen_cursors) - 1]
            return httpx.Response(
                200,
                json={"data": {"actor": {"account": {"aiIssues": {"issues": page}}}}},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(newrelic_issues.httpx, "AsyncClient", lambda **kw: FakeClient())
    integration = Integration(
        project="gcm", env="prod", provider="newrelic",
        config={"account_id": "4618958"},
        encrypted_secrets={"api_key": encryptor.encrypt("nr-key")},
    )
    alarms = await newrelic_issues.NewRelicIssueFetcher(encryptor).list_alarms(integration)

    assert [a.name for a in alarms] == ["First", "Second"]
    assert seen_cursors == [None, "page-2"]


def _issue(issue_id: str, condition: str) -> dict:
    # Real accounts return these string-ish fields as lists; mirrored so the test exercises the
    # same unwrapping the adapter does.
    return {
        "issueId": issue_id,
        "title": [f"Log query result is > 0 on '{condition}'"],
        "priority": "CRITICAL",
        "state": "ACTIVATED",
        "conditionName": [condition],
        "policyName": ["GCM-SES-Alerts"],
    }


def test_the_query_is_valid_json_serialisable():
    """It travels as a JSON string; a stray control character breaks every poll at once."""
    json.dumps({"query": _QUERY})
