"""ManageIntegrations: CRUD for integrations, plus the test-connection action.

Replaces ManageCloudConnections and ManageAdoConnections, which were the same use case written
twice for two providers. What is provider-specific now lives in data, validated against the
registry's declaration rather than against a hard-coded list of clouds:

- `config` must contain every key the provider declares as required, and may not contain unknown
  ones — a typo like `regoin` would otherwise be stored happily and only surface much later as a
  confusing adapter failure.
- Secrets arrive as plaintext keyed by the provider's declared names, and are encrypted one value
  at a time here. On update, a secret the caller omits keeps its stored value, so editing a region
  doesn't require re-typing a PAT nobody has to hand.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.domain.integrations.entities import ALARMS, TICKETS, Integration
from app.domain.integrations.ports import IntegrationRepository, UnsupportedCapabilityError
from app.domain.projects.errors import UnknownProjectError
from app.domain.shared import UnitOfWork
from app.infrastructure.integrations.registry import ProviderRegistry
from app.infrastructure.security.encryptor import Encryptor


class InvalidIntegrationError(Exception):
    """The submitted provider/config/capabilities don't make sense together. Carries a message
    meant for the person filling in the form."""


@dataclass
class ManageIntegrations:
    integrations: IntegrationRepository
    registry: ProviderRegistry
    encryptor: Encryptor
    uow: UnitOfWork

    async def list(self) -> list[Integration]:
        return await self.integrations.list()

    async def get(self, integration_id: uuid.UUID) -> Integration | None:
        return await self.integrations.get(integration_id)

    async def create(
        self,
        *,
        project: str,
        env: str,
        provider: str,
        config: dict,
        secrets: dict[str, str],
        capabilities: tuple[str, ...],
        display_name: str | None = None,
    ) -> Integration:
        capabilities = self._validate(provider, config, capabilities)
        try:
            integration = await self.integrations.add(
                Integration(
                    project=project,
                    env=env,
                    provider=provider,
                    display_name=display_name,
                    config=config,
                    encrypted_secrets=self._encrypt(provider, secrets),
                    capabilities=capabilities,
                )
            )
        except IntegrityError as exc:
            await self.uow.rollback()
            raise UnknownProjectError(project) from exc
        await self.uow.commit()
        return integration

    async def update(
        self,
        integration_id: uuid.UUID,
        *,
        project: str,
        env: str,
        config: dict,
        secrets: dict[str, str],
        capabilities: tuple[str, ...],
        display_name: str | None = None,
    ) -> Integration:
        existing = await self.integrations.get(integration_id)
        if existing is None:
            raise ValueError(f"integration {integration_id} not found")
        # The provider is not editable: changing it would leave config and secrets shaped for the
        # old one. Deleting and re-adding is the honest way to swap providers.
        capabilities = self._validate(existing.provider, config, capabilities)
        merged_secrets = {
            **existing.encrypted_secrets,
            **self._encrypt(existing.provider, secrets),
        }
        try:
            updated = await self.integrations.update(
                Integration(
                    id=integration_id,
                    project=project,
                    env=env,
                    provider=existing.provider,
                    display_name=display_name,
                    config=config,
                    encrypted_secrets=merged_secrets,
                    capabilities=capabilities,
                    enabled=existing.enabled,
                )
            )
        except IntegrityError as exc:
            await self.uow.rollback()
            raise UnknownProjectError(project) from exc
        await self.uow.commit()
        return updated

    async def delete(self, integration_id: uuid.UUID) -> None:
        await self.integrations.delete(integration_id)
        await self.uow.commit()

    async def set_enabled(self, integration_id: uuid.UUID, enabled: bool) -> Integration:
        integration = await self.integrations.set_enabled(integration_id, enabled)
        await self.uow.commit()
        return integration

    async def test(self, integration_id: uuid.UUID) -> tuple[bool, str | None]:
        """Prove the stored credentials work by doing the real thing — one live call through a
        capability the provider actually supports. A bare "can we authenticate" probe would pass
        on credentials that authenticate but lack the permission that matters."""
        integration = await self.integrations.get(integration_id)
        if integration is None:
            raise ValueError(f"integration {integration_id} not found")
        # Each capability has its own idea of "the real thing": fetching alarms for a monitoring
        # source, reading the destination project back for a tracker. Tried in declaration order,
        # so a provider that gains a second capability keeps the test it already had.
        probes = (
            (ALARMS, lambda: self.registry.alarm_source(integration.provider).list_alarms(integration)),
            (TICKETS, lambda: self.registry.ticket_sink(integration.provider).verify(integration)),
        )
        spec = self.registry.spec(integration.provider)
        for capability, probe in probes:
            if capability not in spec.capabilities:
                continue
            try:
                await probe()
            except UnsupportedCapabilityError:
                continue  # advertised but not implemented yet — try the next capability
            except Exception as exc:  # noqa: BLE001 - reported as a test result, not raised
                return False, str(exc)
            return True, None
        return False, f"No test available for {integration.provider} yet"

    def _validate(self, provider: str, config: dict, capabilities: tuple[str, ...]) -> tuple[str, ...]:
        spec = self.registry.spec(provider)

        missing = [k for k in spec.required_config if not str(config.get(k, "")).strip()]
        if missing:
            raise InvalidIntegrationError(
                f"{spec.label} needs {', '.join(missing)}"
            )
        unknown_capabilities = set(capabilities) - spec.capabilities
        if unknown_capabilities:
            raise InvalidIntegrationError(
                f"{spec.label} cannot do {', '.join(sorted(unknown_capabilities))}"
            )
        # An integration with no capability would be stored, polled by nothing, and look broken
        # for reasons the UI could never explain.
        return capabilities or tuple(sorted(spec.capabilities))

    def _encrypt(self, provider: str, secrets: dict[str, str]) -> dict[str, str]:
        allowed = set(self.registry.spec(provider).secret_names)
        return {
            name: self.encryptor.encrypt(value)
            for name, value in secrets.items()
            if name in allowed and value
        }
