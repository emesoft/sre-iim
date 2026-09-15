"""Mappers: Integration domain entities -> response DTOs."""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.integrations.entities import Integration, IntegrationHealth
from app.infrastructure.integrations.registry import ProviderSpec
from app.interface.http.dto.response.integration import (
    CapabilityHealthOut,
    IntegrationOut,
    ProviderOut,
)


def integration_out(
    integration: Integration, health: Sequence[IntegrationHealth] = ()
) -> IntegrationOut:
    return IntegrationOut(
        id=integration.id,
        project=integration.project,
        env=integration.env,
        provider=integration.provider,
        display_name=integration.display_name,
        enabled=integration.enabled,
        config=integration.config,
        # Names only, never values: which credentials are stored is useful to show, the
        # credentials themselves must never travel back to the browser.
        secret_names=sorted(integration.encrypted_secrets),
        capabilities=list(integration.capabilities),
        health=[
            CapabilityHealthOut(
                capability=h.capability,
                last_run_at=h.last_run_at,
                status=h.status,
                error=h.error,
                item_count=h.item_count,
            )
            for h in sorted(health, key=lambda h: h.capability)
        ],
        created_at=integration.created_at,
    )


def provider_out(spec: ProviderSpec, provider: str) -> ProviderOut:
    return ProviderOut(
        provider=provider,
        label=spec.label,
        capabilities=sorted(spec.capabilities),
        required_config=list(spec.required_config),
        secret_names=list(spec.secret_names),
        notes=spec.notes,
    )
