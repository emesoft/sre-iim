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


class AdoTicketClient:
    """TicketClient backed by Azure DevOps work items, scoped to one org/project/PAT."""

    def __init__(self, *, org: str, project: str, pat: str, work_item_type: str = "Bug") -> None:
        self._org = org
        self._project = project
        self._pat = pat
        self._work_item_type = work_item_type

    async def create_ticket(self, title: str, description: str) -> str:
        url = (
            f"https://dev.azure.com/{self._org}/{self._project}/_apis/wit/workitems/"
            f"${self._work_item_type}?api-version=7.1"
        )
        patch = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": description},
        ]
        async with httpx.AsyncClient(auth=("", self._pat), timeout=30.0) as client:
            resp = await client.post(
                url,
                json=patch,
                headers={"Content-Type": "application/json-patch+json"},
            )
            resp.raise_for_status()
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
            resp.raise_for_status()
