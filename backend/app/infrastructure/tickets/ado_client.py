"""Azure DevOps adapter — implements the domain `TicketClient` port.

A thin `httpx` call to the ADO REST API (Work Items), matching the DeepSeek/Jina precedent of a
raw REST call over a vendor SDK. Auth is a PAT over HTTP Basic (empty username, PAT as password).

Configured per internal project (EVP, rxdevs, ...) via the `ado_connections` table, not a single
global org/project/PAT — resolved by the caller (see `interface/http/incidents.py`'s
`create_incident_ticket`) before constructing this client, since that resolution needs a DB
session this adapter itself doesn't hold.

ADO Work Items REST API: https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/create
"""

from __future__ import annotations

import httpx


class AdoApiError(Exception):
    """Azure DevOps rejected the request — carries its own error message (e.g. "Bug" isn't a
    valid work item type for this project's process template) instead of a bare HTTP status."""


def _raise_for_status_with_ado_message(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    try:
        message = resp.json().get("message")
    except ValueError:
        message = None
    raise AdoApiError(message or f"Azure DevOps returned HTTP {resp.status_code}: {resp.text[:500]}")


def _extract_work_item_id(ticket_url: str) -> str | None:
    """Pulls the numeric work item id off the tail of a `.../_workitems/edit/{id}` html link
    (what `create_ticket` stores as `ticket_url`) — `None` if the URL doesn't end in one."""
    tail = ticket_url.rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else None


class AdoTicketClient:
    """TicketClient backed by Azure DevOps work items, scoped to one org/project/PAT."""

    def __init__(self, *, org: str, project: str, pat: str, work_item_type: str = "Bug") -> None:
        self._org = org
        self._project = project
        self._pat = pat
        self._work_item_type = work_item_type

    async def create_ticket(
        self, title: str, description: str, *, related_url: str | None = None
    ) -> str:
        url = (
            f"https://dev.azure.com/{self._org}/{self._project}/_apis/wit/workitems/"
            f"${self._work_item_type}?api-version=7.1"
        )
        patch = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": description},
        ]
        related_id = _extract_work_item_id(related_url) if related_url else None
        if related_id is not None:
            patch.append(
                {
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "System.LinkTypes.Related",
                        "url": f"https://dev.azure.com/{self._org}/_apis/wit/workItems/{related_id}",
                    },
                }
            )
        async with httpx.AsyncClient(auth=("", self._pat), timeout=30.0) as client:
            resp = await client.post(
                url,
                json=patch,
                headers={"Content-Type": "application/json-patch+json"},
            )
            _raise_for_status_with_ado_message(resp)
            data = resp.json()

        html_link = data.get("_links", {}).get("html", {}).get("href")
        if html_link:
            return html_link
        return f"https://dev.azure.com/{self._org}/{self._project}/_workitems/edit/{data['id']}"

    async def verify(self) -> None:
        """A minimal read-only call (fetch the project) to confirm the org/project/PAT actually
        work — used by the Settings page's "Test" button. Raises on any failure (bad PAT, wrong
        org/project name, network error); the caller reports the exception message."""
        url = f"https://dev.azure.com/{self._org}/_apis/projects/{self._project}?api-version=7.1"
        async with httpx.AsyncClient(auth=("", self._pat), timeout=15.0) as client:
            resp = await client.get(url)
            _raise_for_status_with_ado_message(resp)
