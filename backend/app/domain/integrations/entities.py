"""Domain entities for integrations — one external system wired to one project.

Replaces the per-provider `CloudConnection`/`AdoConnection` shapes with a provider-agnostic one:
what every provider has in common stays as fields, and what only one provider has lives in
`config` (non-secret) and `encrypted_secrets`. See migration 0019 for why.

Plain dataclasses, no ORM/framework coupling — same convention as domain/incidents/entities.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

#: What an integration can be used for. A provider supports a subset of these (see the registry in
#: infrastructure/integrations/registry.py) and an individual integration enables a subset of what
#: its provider supports — one AWS credential can poll alarms, read logs and (later) read cost,
#: while a New Relic one only supplies alarms.
ALARMS = "alarms"
LOGS = "logs"
TICKETS = "tickets"
COST = "cost"


@dataclass
class Integration:
    """One provider connected to one project/environment.

    `config` and `encrypted_secrets` are provider-shaped: the provider's adapter is the only code
    that knows their keys, which is what stops a second provider from being forced through the
    first one's fields. Secrets are stored already-encrypted, one value at a time; nothing here
    decrypts — that happens in the adapter, at the moment of use.
    """

    project: str
    env: str
    provider: str  # aws | newrelic | azure_devops
    config: dict = field(default_factory=dict)
    encrypted_secrets: dict = field(default_factory=dict)
    capabilities: tuple[str, ...] = ()
    enabled: bool = True
    display_name: str | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def label(self) -> str:
        """What to call this integration in a log line or an error message."""
        return self.display_name or f"{self.provider}:{self.project}/{self.env}"


@dataclass(frozen=True)
class IntegrationHealth:
    """The last run of one capability of one integration.

    Per-capability rather than per-integration because they fail independently: a cost sync
    failing on expired billing permissions says nothing about whether alarm polling still works,
    and a single status field would have to pick one of those to report.
    """

    integration_id: uuid.UUID
    capability: str
    last_run_at: datetime | None = None
    status: str | None = None  # ok | error
    error: str | None = None
    item_count: int | None = None


@dataclass
class TrackedAlarm:
    """Last-seen state of one alarm for one integration, and the incident it opened (if any), so
    the poller can tell OK->ALARM from an alarm that's already open.

    `incident_id` is the currently-open incident for this alarm — cleared back to None once the
    alarm returns to OK and the incident is auto-resolved. `last_incident_id`/`occurrence_count`
    are NOT cleared on resolve, so the next OK->ALARM transition for this same alarm ARN can link
    the new incident back to the previous one and report how many times it has recurred."""

    #: The integration this alarm was seen through (`integrations.id`).
    connection_id: uuid.UUID
    alarm_arn: str
    alarm_name: str
    last_state: str  # OK | ALARM
    incident_id: uuid.UUID | None = None
    last_incident_id: uuid.UUID | None = None
    occurrence_count: int = 0
    id: uuid.UUID | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AlarmState:
    """One alarm's current state, as read from a provider's alarm/issue API for this poll cycle
    (CloudWatch's `describe_alarms`, or New Relic's NerdGraph `aiIssues`).

    Lives here rather than under one provider because it is the shared vocabulary every
    `AlarmSource` speaks — the poller's state machine works on these, not on provider payloads.
    """

    arn: str
    name: str
    state: str  # OK | ALARM | INSUFFICIENT_DATA
    reason: str | None = None
    metric_name: str | None = None
    namespace: str | None = None
    # Extra human-readable detail beyond `name`, when the provider has more to say than just the
    # short name (e.g. New Relic's full templated issue title). None when there's nothing more —
    # callers building an alert description should skip this rather than repeat `name` verbatim.
    detail: str | None = None
    # Priority as reported by the provider, lowercased (critical | high | medium | low), and None
    # when the provider has no such concept. New Relic issues carry one; CloudWatch alarms do not,
    # so a CloudWatch alarm only has a priority if its own name spells one out. This gates
    # automatic analysis (AutoAnalyzeIncidents) — None means "the provider never said this was
    # urgent", which is deliberately not the same as low.
    priority: str | None = None
