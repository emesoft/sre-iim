"""Ports for integrations, split by **capability** rather than by provider.

The previous shape had one `AlarmFetcher` and a dispatcher that switched on `connection.cloud`.
That works while every provider does exactly one thing, and stops working the moment one
credential serves several jobs (AWS: alarms + logs + cost) while another serves one (New Relic:
alarms). Naming the capability instead of the vendor means a provider implements only the ports it
actually supports, and adding Azure or Sentry is a matter of implementing ports and registering
them — no existing code learns a new vendor name.

Same dependency-inversion convention as domain/incidents/ports.py.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.domain.integrations.entities import AlarmState, Integration, IntegrationHealth, TrackedAlarm

__all__ = [
    "AlarmSource",
    "IntegrationRepository",
    "TicketSink",
    "TrackedAlarmRepository",
    "UnsupportedCapabilityError",
]


class UnsupportedCapabilityError(Exception):
    """Asked a provider for something it can't do (e.g. cost from New Relic). A programming error
    at the call site, not a runtime condition to handle — the registry knows the answer up front
    via `supports()`."""


class AlarmSource(Protocol):
    """Reads the current alarm/alert states for one integration.

    Returns *every* alarm it can see, in ALARM and OK alike: the poller diffs these against its
    tracked state, so an alarm that has recovered has to appear as OK rather than simply vanish —
    otherwise the incident it opened would never be closed.
    """

    async def list_alarms(self, integration: Integration) -> list[AlarmState]: ...


class TicketSink(Protocol):
    """Files tickets for one provider, resolving the destination from the integration itself.

    Split from `incidents.TicketClient` (which is already scoped to one org/project) so the
    registry can build one adapter per provider rather than one per integration — and so
    `verify()` has somewhere to live: proving the stored credentials work is a property of the
    provider, not of the incident being filed.
    """

    async def verify(self, integration: Integration) -> None:
        """One real read-only call. Raises with the provider's own message on any failure."""
        ...

    def client_for(self, integration: Integration) -> object:
        """A TicketClient bound to this integration's destination."""
        ...


class IntegrationRepository(Protocol):
    """Persistence for integrations and their per-capability health."""

    async def get(self, integration_id: uuid.UUID) -> Integration | None: ...

    async def list(
        self, *, capability: str | None = None, enabled_only: bool = False
    ) -> list[Integration]:
        """`capability` filters to integrations that declare it; `enabled_only` drops the ones
        paused by a human. The scheduled sweep asks for both; a settings screen asks for neither."""
        ...

    async def for_project(self, project: str, capability: str) -> Integration | None:
        """The integration to use when a project needs one thing done (filing a ticket, fetching
        logs). `None` means the project has nothing wired up for that capability yet."""
        ...

    async def for_provider(self, project: str, provider: str) -> Integration | None:
        """The project's integration with a given vendor, whatever capabilities it carries.

        Distinct from `for_project(project, capability)`: asking for "an alarms integration" and
        then checking whether it happens to be AWS silently answers "no AWS here" for a project
        whose alarms come from New Relic *and* which has an AWS account attached for logs. The
        AWS tools need the account, not the alarm feed.
        """
        ...

    async def add(self, integration: Integration) -> Integration: ...

    async def update(self, integration: Integration) -> Integration:
        """Full replacement of the editable fields; `integration.id` selects the row."""
        ...

    async def delete(self, integration_id: uuid.UUID) -> None: ...

    async def set_enabled(self, integration_id: uuid.UUID, enabled: bool) -> Integration: ...

    async def health(self, integration_id: uuid.UUID, capability: str) -> IntegrationHealth | None: ...

    async def health_for(self, integration_id: uuid.UUID) -> list[IntegrationHealth]:
        """Every capability's last run for one integration — the Settings card shows them all."""
        ...

    async def record_run(
        self,
        integration_id: uuid.UUID,
        capability: str,
        *,
        status: str,
        error: str | None,
        item_count: int | None = None,
    ) -> None:
        """Record the outcome of running one capability, stamped now."""
        ...


class TrackedAlarmRepository(Protocol):
    """Per-alarm polling state: what each alarm looked like last time, and which incident it
    opened. This is what makes the poller a state machine rather than a re-notifier."""

    async def get(self, connection_id: uuid.UUID, alarm_arn: str) -> TrackedAlarm | None: ...

    async def upsert(
        self,
        connection_id: uuid.UUID,
        *,
        alarm_arn: str,
        alarm_name: str,
        last_state: str,
        incident_id: uuid.UUID | None,
        last_incident_id: uuid.UUID | None = None,
        occurrence_count: int | None = None,
    ) -> None:
        """`last_incident_id`/`occurrence_count` are only updated when explicitly passed (not
        None) — the OK-transition upsert call passes neither, leaving the alarm's recurrence
        history untouched while only `incident_id` resets to None."""
        ...
