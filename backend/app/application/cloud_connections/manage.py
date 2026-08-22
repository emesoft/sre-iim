"""ManageCloudConnections: CRUD for cloud connections plus a test-connection action.

Encrypts access-key credentials on create; API DTOs (interface layer) never round-trip the
encrypted fields back out, so decryption only ever happens inside CredentialResolver at call time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.cloud_connections.entities import CloudConnection
from app.domain.cloud_connections.ports import AlarmFetcher, CloudConnectionRepository
from app.domain.shared import UnitOfWork
from app.infrastructure.security.encryptor import Encryptor


@dataclass
class ManageCloudConnections:
    connections: CloudConnectionRepository
    encryptor: Encryptor
    fetcher: AlarmFetcher
    uow: UnitOfWork

    async def create(
        self,
        *,
        project: str,
        env: str,
        region: str,
        auth_type: str,
        sso_profile_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> CloudConnection:
        connection = await self.connections.add(
            CloudConnection(
                project=project,
                env=env,
                region=region,
                auth_type=auth_type,
                sso_profile_name=sso_profile_name,
                encrypted_access_key_id=(
                    self.encryptor.encrypt(access_key_id) if access_key_id else None
                ),
                encrypted_secret_access_key=(
                    self.encryptor.encrypt(secret_access_key) if secret_access_key else None
                ),
            )
        )
        await self.uow.commit()
        return connection

    async def list(self) -> list[CloudConnection]:
        return await self.connections.list()

    async def update(
        self,
        connection_id: uuid.UUID,
        *,
        project: str,
        env: str,
        region: str,
        auth_type: str,
        sso_profile_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> CloudConnection:
        """Edit an existing connection. For auth_type="access_key", omitting access_key_id/
        secret_access_key (both None) keeps the currently-stored credentials — lets a user fix a
        typo in project/env/region without re-entering a secret they don't have handy. Switching
        to auth_type="sso" always clears any stored access-key credentials."""
        existing = await self.connections.get(connection_id)
        if existing is None:
            raise ValueError(f"connection {connection_id} not found")

        if auth_type == "sso":
            encrypted_access_key_id = None
            encrypted_secret_access_key = None
        elif access_key_id and secret_access_key:
            encrypted_access_key_id = self.encryptor.encrypt(access_key_id)
            encrypted_secret_access_key = self.encryptor.encrypt(secret_access_key)
        else:
            encrypted_access_key_id = existing.encrypted_access_key_id
            encrypted_secret_access_key = existing.encrypted_secret_access_key

        updated = CloudConnection(
            id=connection_id,
            project=project,
            env=env,
            region=region,
            auth_type=auth_type,
            sso_profile_name=sso_profile_name,
            encrypted_access_key_id=encrypted_access_key_id,
            encrypted_secret_access_key=encrypted_secret_access_key,
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
        try:
            await self.fetcher.list_alarms(connection)
            return True, None
        except Exception as exc:  # noqa: BLE001 - reported to the caller as a test result, not raised
            return False, str(exc)
