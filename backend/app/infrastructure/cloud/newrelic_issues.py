"""Reads active New Relic alert issues for one connection via NerdGraph (GraphQL over HTTPS) —
async httpx call, same pattern as infrastructure/llm/chat.py's DeepSeekChatModel.

NOTE: NerdGraph's `aiIssues` schema below reflects New Relic's documented shape as of this
writing, but hasn't been exercised against a live account in this environment (no New Relic
credentials available here). If it comes back empty or errors once a real API key is wired in,
check `last_poll_error` on the connection (Settings page) first — a schema drift (New Relic has
changed `aiIssues` field names before) is the most likely cause, not a bug in the polling flow
itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.domain.integrations.entities import AlarmState, Integration
from app.infrastructure.security.encryptor import Encryptor

_NERDGRAPH_URL = "https://api.newrelic.com/graphql"
#: A stop, not a limit — an account this noisy has a different problem than pagination.
_MAX_PAGES = 20

#: How far back to ask for issues.
#:
#: `aiIssues.issues` applies its own default window when none is given, and that default is about a
#: day: a live account returned 4 issues unwindowed and 40 over seven days. An issue that activated
#: two days ago and is *still open* therefore never appeared, and no incident was ever created for
#: it — the exact failure this poller exists to prevent, and silent, because a short list looks the
#: same as a quiet account.
_LOOKBACK = timedelta(days=7)

# No `states` filter: we need both currently-open and recently-closed issues in one response, the
# same way CloudWatch's describe_alarms returns every alarm regardless of state — a closed issue
# that's simply absent from the response would never be seen transitioning back to OK, and the
# incident it opened would stay open forever.
_QUERY = """
query($accountId: Int!, $start: EpochMilliseconds!, $end: EpochMilliseconds!, $cursor: String) {
  actor {
    account(id: $accountId) {
      aiIssues {
        issues(timeWindow: {startTime: $start, endTime: $end}, cursor: $cursor) {
          nextCursor
          issues {
            issueId
            title
            priority
            state
            conditionName
            policyName
          }
        }
      }
    }
  }
}
"""


def _first_str(value: object) -> str | None:
    """NerdGraph `aiIssues` string-ish fields (`title`, `conditionName`, `policyName`) all come
    back as `[String]` on real accounts — a list, since one issue can correlate multiple
    conditions/policies — even though not every one of them is documented that way. Verified
    empirically against a live account: `conditionName` alone being a plain string was a wrong
    assumption that crashed `tracked_alarms.alarm_name` (a VARCHAR column) with a list value.
    Apply this to every such field rather than guessing per-field which ones are lists."""
    if isinstance(value, list):
        return str(value[0]) if value else None
    if isinstance(value, str) and value:
        return value
    return None


def _issue_name(issue: dict) -> str:
    # conditionName is short and specific (e.g. "CRITICAL - SES Errors"); title is often a whole
    # templated sentence (e.g. "Log query result is > 0.0 on 'CRITICAL - SES Errors'") that can
    # itself contain a quoted condition name — using it as-is would break the incident-list
    # headline extraction, which looks for the first 'quoted' substring in the alert text.
    return (
        _first_str(issue.get("conditionName"))
        or _first_str(issue.get("title"))
        or _first_str(issue.get("policyName"))
        or "New Relic issue"
    )


class NewRelicIssueFetcher:
    """Fetches alert issues for one New Relic account, filtered to CRITICAL priority — polling
    only pulls what's actually worth paging someone for, same reasoning as the CloudWatch fetcher
    only ever seeing alarms already configured in that account.

    Config: `account_id`. Secret: `api_key` (a NerdGraph user key) — both declared alongside
    this adapter in the provider registry."""

    def __init__(self, encryptor: Encryptor) -> None:
        self._encryptor = encryptor

    async def _fetch_all(self, api_key: str, account_id: int) -> list[dict]:
        """Every issue in the window, following the cursor.

        One page is not the whole answer: a busy week came back capped, and a truncated page is
        indistinguishable from a calm account — the alarms that fell off the end simply never
        become incidents.
        """
        now = datetime.now(timezone.utc)
        variables = {
            "accountId": account_id,
            "start": int((now - _LOOKBACK).timestamp() * 1000),
            "end": int(now.timestamp() * 1000),
            "cursor": None,
        }
        found: list[dict] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for _page in range(_MAX_PAGES):
                resp = await client.post(
                    _NERDGRAPH_URL,
                    json={"query": _QUERY, "variables": variables},
                    headers={"API-Key": api_key, "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("errors"):
                    raise RuntimeError(f"New Relic NerdGraph error: {data['errors']}")
                page = (
                    (data.get("data") or {})
                    .get("actor", {})
                    .get("account", {})
                    .get("aiIssues", {})
                    .get("issues", {})
                )
                found.extend(page.get("issues") or [])
                cursor = page.get("nextCursor")
                if not cursor:
                    break
                variables["cursor"] = cursor
        return found

    async def list_alarms(self, integration: Integration) -> list[AlarmState]:
        encrypted_key = (integration.encrypted_secrets or {}).get("api_key")
        if not encrypted_key:
            raise ValueError("New Relic integration has no API key configured")
        api_key = self._encryptor.decrypt(encrypted_key)
        raw_account_id = (integration.config or {}).get("account_id")
        try:
            account_id = int(raw_account_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"New Relic integration's account_id is not a number: {raw_account_id!r}"
            ) from exc

        issues = await self._fetch_all(api_key, account_id)

        alarms: list[AlarmState] = []
        for issue in issues:
            priority = (issue.get("priority") or "").upper()
            if priority != "CRITICAL":
                continue
            issue_id = issue.get("issueId")
            if not issue_id:
                continue
            name = _issue_name(issue)
            title = _first_str(issue.get("title"))
            alarms.append(
                AlarmState(
                    arn=str(issue_id),
                    name=name,
                    state="OK" if (issue.get("state") or "").upper() == "CLOSED" else "ALARM",
                    reason=_first_str(issue.get("policyName")),
                    metric_name=_first_str(issue.get("conditionName")),
                    namespace=None,
                    # New Relic's title is often a fuller sentence than the short condition name
                    # (e.g. "Log query result is > 0.0 on 'CRITICAL - Yelp Errors'" vs just
                    # "CRITICAL - Yelp Errors") — worth surfacing as extra detail, but only when it
                    # actually adds something; otherwise the incident's title and description would
                    # just repeat the same short phrase back at each other.
                    detail=title if title and title != name else None,
                    # Every issue reaching here passed the CRITICAL filter above; carried through
                    # explicitly so auto-analysis reads a provider-reported priority rather than
                    # re-deriving what that filter already decided.
                    priority=priority.lower(),
                )
            )
        return alarms
