"""The provider registry: the one place that knows which provider can do what.

This replaces `DispatchingAlarmFetcher`'s `if connection.cloud == ...` chain. The difference that
matters isn't the shape of the lookup, it's that capability support is now *declared data* rather
than control flow spread across call sites — the poller asks "give me an alarm source for this
provider", the Settings UI asks "which capability toggles should this provider show", and both
read the same table.

Adding a provider means adding one entry here plus its adapter. Adding a capability means adding
its port, its factory field, and an accessor — no existing provider entry has to change, and
nothing outside this module learns a new vendor name.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain.integrations.entities import ALARMS, COST, LOGS, TICKETS
from app.domain.integrations.ports import AlarmSource, TicketSink, UnsupportedCapabilityError
from app.infrastructure.cloud.cloudwatch_alarms import CloudWatchAlarmFetcher
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.cloud.newrelic_issues import NewRelicIssueFetcher
from app.infrastructure.security.encryptor import Encryptor
from app.infrastructure.tickets.ado_source import AdoTicketSink

AWS = "aws"
NEWRELIC = "newrelic"
AZURE_DEVOPS = "azure_devops"


@dataclass(frozen=True)
class ProviderSpec:
    """What one provider offers.

    `capabilities` is the full advertised set — what the Settings UI may offer as toggles. A
    capability listed here without a factory below is advertised but not yet implemented; asking
    for it raises rather than silently doing nothing, so a half-wired provider fails loudly at the
    call site instead of looking like it simply found no data.
    """

    label: str
    capabilities: frozenset[str]
    alarm_source: Callable[[Encryptor], AlarmSource] | None = None
    ticket_sink: Callable[[Encryptor], TicketSink] | None = None
    #: Config keys the adapter needs, for the Settings form and for validation.
    required_config: tuple[str, ...] = ()
    #: Secret names, likewise. Values are encrypted at rest, one per name.
    secret_names: tuple[str, ...] = ()
    notes: str = ""


_SPECS: dict[str, ProviderSpec] = {
    AWS: ProviderSpec(
        label="AWS",
        # `cost` is advertised because the product roadmap has it and the Settings UI should show
        # it as available-but-off; there is no adapter yet, so asking for it raises.
        capabilities=frozenset({ALARMS, LOGS, COST}),
        alarm_source=lambda encryptor: CloudWatchAlarmFetcher(CredentialResolver(encryptor)),
        required_config=("region", "auth_type"),
        secret_names=(
            "access_key_id",
            "secret_access_key",
            # Written by the in-app SSO flow, never typed into the form — see
            # infrastructure/cloud/aws_sso.py. Declared here so the secrets-are-write-only rules
            # and the "which credentials exist" reporting cover them like any other.
            "sso_client_id",
            "sso_client_secret",
            "sso_refresh_token",
        ),
        notes=(
            "Sign in with AWS SSO from this page (nothing needed on the host), a profile from the "
            "host's ~/.aws/config, or a stored access key pair."
        ),
    ),
    NEWRELIC: ProviderSpec(
        label="New Relic",
        capabilities=frozenset({ALARMS}),
        alarm_source=lambda encryptor: NewRelicIssueFetcher(encryptor),
        required_config=("account_id",),
        secret_names=("api_key",),
        notes="NerdGraph user API key; only CRITICAL issues are polled.",
    ),
    AZURE_DEVOPS: ProviderSpec(
        label="Azure DevOps",
        capabilities=frozenset({TICKETS}),
        ticket_sink=lambda encryptor: AdoTicketSink(encryptor),
        required_config=("organization", "ado_project", "work_item_type"),
        secret_names=("pat",),
        notes="Personal access token with work-item write scope.",
    ),
}


@dataclass
class ProviderRegistry:
    encryptor: Encryptor
    _specs: dict[str, ProviderSpec] = field(default_factory=lambda: dict(_SPECS))

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def spec(self, provider: str) -> ProviderSpec:
        try:
            return self._specs[provider]
        except KeyError as exc:
            raise UnsupportedCapabilityError(f"unknown provider: {provider!r}") from exc

    def capabilities(self, provider: str) -> frozenset[str]:
        return self.spec(provider).capabilities

    def supports(self, provider: str, capability: str) -> bool:
        return provider in self._specs and capability in self._specs[provider].capabilities

    def alarm_source(self, provider: str) -> AlarmSource:
        factory = self.spec(provider).alarm_source
        if factory is None:
            raise UnsupportedCapabilityError(f"{provider!r} cannot supply alarms")
        return factory(self.encryptor)

    def ticket_sink(self, provider: str) -> TicketSink:
        factory = self.spec(provider).ticket_sink
        if factory is None:
            raise UnsupportedCapabilityError(f"{provider!r} cannot file tickets")
        return factory(self.encryptor)
