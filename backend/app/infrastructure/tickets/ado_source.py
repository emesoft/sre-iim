"""Azure DevOps as a registry-declared provider: builds an `AdoTicketClient` from an Integration.

The client itself is scoped to one org/project/PAT and knows nothing about how those were stored.
This is the piece that reads them off the integration row and decrypts the PAT — one construction
site, so the Settings "Test" button and the ticket-filing route can't disagree about, say, the
default work item type.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.integrations.entities import Integration
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.tickets.ado_client import AdoTicketClient


class MissingCredentialError(Exception):
    """The integration row has no PAT — reported as a failed test, not raised at the caller."""


@dataclass
class AdoTicketSink:
    encryptor: Encryptor

    def client_for(self, integration: Integration) -> AdoTicketClient:
        config = integration.config or {}
        if "pat" not in (integration.encrypted_secrets or {}):
            # Reads far better on the Settings page than the KeyError this used to be, and it's
            # the likely state right after someone edits the org but leaves the PAT field blank.
            raise MissingCredentialError(
                "No personal access token stored — edit this integration and paste one."
            )
        return AdoTicketClient(
            org=config.get("organization", ""),
            project=config.get("ado_project", ""),
            pat=self.encryptor.decrypt(integration.encrypted_secrets["pat"]),
            work_item_type=config.get("work_item_type", "Bug"),
        )

    async def verify(self, integration: Integration) -> None:
        """Reads the ADO project back. Deliberately not a write: a "Test" button that files a work
        item would leave litter in the tracker every time someone checks a PAT."""
        await self.client_for(integration).verify()
