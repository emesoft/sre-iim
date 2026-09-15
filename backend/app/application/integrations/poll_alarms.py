"""PollAlarmsJob: the ALARM<->OK state machine that turns provider alarms into incidents.

Works on `Integration` and asks the provider registry for an `AlarmSource`, so it holds no
knowledge of which vendors exist — adding one is a registry entry plus an adapter, and this file
doesn't change. The incident's `source` and human-readable `alert` text still read differently per
provider (see `_incident_source`/`_build_alert_context`) so a New Relic-sourced incident doesn't
claim to be a CloudWatch alarm.

Per-integration failures are isolated: one expired SSO token must not stop the others from being
polled — see design spec "Polling & state machine".

A new alarm only calls `create_incident` (status="new"); it does NOT analyze. The alarm's raw
reason/metric already makes a useful incident card, and an LLM call per alarm is a bill nobody
asked for. Urgent ones are picked up separately by AutoAnalyzeIncidents, and anything else waits
for a human to press "Analyze with AI".
"""

from __future__ import annotations

import logging

import uuid
from dataclasses import dataclass

from app.application.incidents.ingest import IngestIncident
from app.application.incidents.resolve import ResolveIncident
from app.domain.integrations.noise import is_scaling_mechanism
from app.domain.integrations.entities import ALARMS, AlarmState, Integration
from app.domain.integrations.ports import IntegrationRepository, TrackedAlarmRepository
from app.domain.shared import UnitOfWork
from app.infrastructure.integrations.registry import NEWRELIC, ProviderRegistry


@dataclass(frozen=True)
class ConnectionPollOutcome:
    """Result of polling one integration — what the HTTP layer needs to answer "did the refresh
    work, and did it find anything?" without a second round-trip to the DB."""

    connection_id: uuid.UUID
    status: str  # ok | error
    error: str | None
    alarm_count: int  # 0 on error — the fetch never got far enough to count anything


def _incident_source(integration: Integration) -> str:
    return "newrelic_issue" if integration.provider == NEWRELIC else "cloudwatch_alarm"


def _build_alert_context(integration: Integration, alarm: AlarmState) -> dict:
    if integration.provider == NEWRELIC:
        # `alarm.name` is ALWAYS wrapped in quotes right after "alert " — the incident-list
        # headline is extracted by finding the first 'quoted' substring in this text (see
        # build_headline in dto/mappers/incident.py), and that extraction must never depend on
        # whether `alarm.detail` happens to also quote the name somewhere later in the sentence.
        description = f"New Relic alert '{alarm.name}'"
        description += f": {alarm.detail}" if alarm.detail else " is active"
        if alarm.reason:
            description += f" (policy: {alarm.reason})"
    else:
        description = f"CloudWatch alarm '{alarm.name}' is in ALARM state"
        if alarm.reason:
            description += f": {alarm.reason}"
    context: dict = {"service": integration.project, "env": integration.env, "alert": description}
    if alarm.metric_name:
        context["metrics"] = {"namespace": alarm.namespace, "metric_name": alarm.metric_name}
    if alarm.priority:
        # Persisted so AutoAnalyzeIncidents can find urgent incidents later, from any ingest path —
        # the alarm object itself is gone by the time that sweep runs.
        context["priority"] = alarm.priority
    return context


logger = logging.getLogger(__name__)


@dataclass
class PollAlarmsJob:
    integrations: IntegrationRepository
    tracked: TrackedAlarmRepository
    registry: ProviderRegistry
    ingest: IngestIncident
    resolve: ResolveIncident
    uow: UnitOfWork
    #: Drop AWS's own target-tracking scaling alarms before they become incidents. A flag rather
    #: than a hard rule so an account that genuinely wants to see them can have them back, but the
    #: default is off-by-noise: an idle service permanently "in ALARM" teaches people to ignore the
    #: queue, which costs far more than the alarms are worth.
    suppress_scaling_noise: bool = True

    async def run(self, *, connection_id: uuid.UUID | None = None) -> list[ConnectionPollOutcome]:
        """Poll every alarm-capable integration, or just one (the per-row "Refresh" button —
        `POST /api/cloud-connections/{id}/poll`). An unknown id is a no-op: the router layer is
        responsible for 404ing before calling this."""
        if connection_id is not None:
            # An explicit "Refresh"/"Test" click always polls, even if the integration is paused —
            # only the unattended scheduled sweep below respects `enabled`.
            one = await self.integrations.get(connection_id)
            targets = [one] if one is not None and one.supports(ALARMS) else []
        else:
            targets = await self.integrations.list(capability=ALARMS, enabled_only=True)

        outcomes: list[ConnectionPollOutcome] = []
        for integration in targets:
            try:
                source = self.registry.alarm_source(integration.provider)
                alarms = await source.list_alarms(integration)
                alarm_count = sum(1 for alarm in alarms if alarm.state == "ALARM")
                for alarm in alarms:
                    await self._apply(integration, alarm)
            except Exception as exc:  # noqa: BLE001 - isolate this one's failure, keep polling others
                # A failure partway through (e.g. a DB flush error inside `_apply`) leaves the
                # session unable to run further statements until it's rolled back — without this,
                # `record_run` below raises `PendingRollbackError` and the whole request 500s
                # instead of isolating just this integration.
                await self.uow.rollback()
                await self.integrations.record_run(
                    integration.id, ALARMS, status="error", error=str(exc)
                )
                await self.uow.commit()
                outcomes.append(
                    ConnectionPollOutcome(
                        integration.id, status="error", error=str(exc), alarm_count=0
                    )
                )
                continue

            await self.integrations.record_run(
                integration.id, ALARMS, status="ok", error=None, item_count=alarm_count
            )
            await self.uow.commit()
            outcomes.append(
                ConnectionPollOutcome(
                    integration.id, status="ok", error=None, alarm_count=alarm_count
                )
            )
        return outcomes

    async def _apply(self, integration: Integration, alarm: AlarmState) -> None:
        if self.suppress_scaling_noise and is_scaling_mechanism(alarm.name):
            # Tracked, not merely skipped: recording the state stops this being re-evaluated every
            # five minutes forever, and leaves a row that explains why an alarm a person can see in
            # the AWS console never appears here.
            logger.debug("suppressed autoscaling alarm %s", alarm.name)
            await self.tracked.upsert(
                integration.id,
                alarm_arn=alarm.arn,
                alarm_name=alarm.name,
                last_state=alarm.state,
                incident_id=None,
            )
            return

        existing = await self.tracked.get(integration.id, alarm.arn)
        was_alarming = existing is not None and existing.last_state == "ALARM"

        if alarm.state == "ALARM" and not was_alarming:
            occurrence_count = (existing.occurrence_count if existing else 0) + 1
            previous_incident_id = existing.last_incident_id if existing else None
            incident = await self.ingest.create_incident(
                source=_incident_source(integration),
                context=_build_alert_context(integration, alarm),
                status="new",
                occurrence_count=occurrence_count,
                previous_incident_id=previous_incident_id,
            )
            await self.tracked.upsert(
                integration.id, alarm_arn=alarm.arn, alarm_name=alarm.name,
                last_state="ALARM", incident_id=incident.id,
                last_incident_id=incident.id, occurrence_count=occurrence_count,
            )
        elif alarm.state == "OK" and was_alarming:
            if existing.incident_id is not None:
                # ResolveIncident.resolve() closes the incident either way — it only saves a
                # known-issue case when there's an analysis to write up, so an alarm-created
                # incident that clears before anyone ran "Analyze with AI" still gets closed
                # instead of staying stuck open.
                kind = "issue" if integration.provider == NEWRELIC else "alarm"
                await self.resolve.resolve(
                    existing.incident_id,
                    resolution_notes=f"Auto-resolved: {kind} '{alarm.name}' returned to OK",
                )
            await self.tracked.upsert(
                integration.id, alarm_arn=alarm.arn, alarm_name=alarm.name,
                last_state="OK", incident_id=None,
            )
