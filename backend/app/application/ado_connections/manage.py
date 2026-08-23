"""ManageAdoConnections: CRUD for per-project Azure DevOps connections plus a test-connection
action. Encrypts the PAT on create/update; API DTOs (interface layer) never round-trip it back
out — same convention as ManageCloudConnections for AWS access keys.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.ado_connections.entities import AdoConnection
from app.domain.ado_connections.ports import AdoConnectionRepository
from app.domain.shared import UnitOfWork
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.tickets.ado_client import AdoTicketClient


@dataclass
class ManageAdoConnections:
    connections: AdoConnectionRepository
    encryptor: Encryptor
    uow: UnitOfWork

    async def create(
        self, *, project: str, org: str, ado_project: str, pat: str, work_item_type: str = "Bug"
    ) -> AdoConnection:
        connection = await self.connections.add(
            AdoConnection(
                project=project,
                org=org,
                ado_project=ado_project,
                encrypted_pat=self.encryptor.encrypt(pat),
                work_item_type=work_item_type,
            )
        )
        await self.uow.commit()
        return connection

    async def list(self) -> list[AdoConnection]:
        return await self.connections.list()

    async def update(
        self,
        connection_id: uuid.UUID,
        *,
        project: str,
        org: str,
        ado_project: str,
        pat: str | None = None,
        work_item_type: str = "Bug",
    ) -> AdoConnection:
        """Edit an existing connection. Omitting `pat` keeps the currently-stored PAT — lets a
        user fix a typo in project/org/ado_project without re-entering a secret they don't have
        handy."""
        existing = await self.connections.get(connection_id)
        if existing is None:
            raise ValueError(f"connection {connection_id} not found")

        encrypted_pat = self.encryptor.encrypt(pat) if pat else existing.encrypted_pat
        updated = AdoConnection(
            id=connection_id,
            project=project,
            org=org,
            ado_project=ado_project,
            encrypted_pat=encrypted_pat,
            work_item_type=work_item_type,
        )
        result = await self.connections.update(updated)
        await self.uow.commit()
        return result

    async def delete(self, connection_id: uuid.UUID) -> None:
        await self.connections.delete(connection_id)
        await self.uow.commit()

    async def test(self, connection_id: uuid.UUID) -> tuple[bool, str | None]:
        connection = await self.connections.get(connection_id)
        if connection is None:
            raise ValueError(f"connection {connection_id} not found")
        client = AdoTicketClient(
            org=connection.org,
            project=connection.ado_project,
            pat=self.encryptor.decrypt(connection.encrypted_pat),
            work_item_type=connection.work_item_type,
        )
        try:
            await client.verify()
            return True, None
        except Exception as exc:  # noqa: BLE001 - reported to the caller as a test result, not raised
            return False, str(exc)
