"""Azure DevOps adapter — implements the domain `TicketClient` port.

A thin `httpx` call to the ADO REST API (Work Items), matching the DeepSeek/Jina precedent of a
raw REST call over a vendor SDK. Auth is a PAT over HTTP Basic (empty username, PAT as password).

ADO Work Items REST API: https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/create
"""

from __future__ import annotations

import httpx

from app.infrastructure.config import Settings


class AdoConfigError(RuntimeError):
    """AZDO_ORG / AZDO_PROJECT / AZDO_PAT are not configured."""


class AdoTicketClient:
    """TicketClient backed by Azure DevOps work items."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def create_ticket(self, title: str, description: str) -> str:
        s = self._settings
        if not (s.azdo_org and s.azdo_project and s.azdo_pat):
            raise AdoConfigError("AZDO_ORG / AZDO_PROJECT / AZDO_PAT are not set")

        url = (
            f"https://dev.azure.com/{s.azdo_org}/{s.azdo_project}/_apis/wit/workitems/"
            f"${s.azdo_work_item_type}?api-version=7.1"
        )
        patch = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": description},
        ]
        async with httpx.AsyncClient(auth=("", s.azdo_pat), timeout=30.0) as client:
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
        return f"https://dev.azure.com/{s.azdo_org}/{s.azdo_project}/_workitems/edit/{data['id']}"
