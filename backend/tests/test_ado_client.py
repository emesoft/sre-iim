"""Unit test for the Azure DevOps ticket client config guard. No network."""

import pytest

from app.infrastructure.config import Settings
from app.infrastructure.tickets.ado_client import AdoConfigError, AdoTicketClient


@pytest.mark.asyncio
async def test_create_ticket_raises_clearly_when_unconfigured():
    settings = Settings(_env_file=None, azdo_org=None, azdo_project=None, azdo_pat=None)
    client = AdoTicketClient(settings)
    with pytest.raises(AdoConfigError):
        await client.create_ticket("title", "description")
