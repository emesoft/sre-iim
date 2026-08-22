"""PollAlarmsJob: the ALARM<->OK state machine that turns CloudWatch alarms into incidents.

Drives the existing IngestIncident/ResolveIncident use cases unchanged (source="cloudwatch_alarm").
Per-connection failures are isolated so one bad connection (expired SSO token, revoked key) doesn't
block the others — see design spec "Polling & state machine".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.incidents.ingest import IngestIncident
from app.application.incidents.resolve import ResolveIncident
from app.domain.cloud_connections.entities import AlarmState, CloudConnection
from app.domain.cloud_connections.ports import (
    AlarmFetcher,
    CloudConnectionRepository,
    TrackedAlarmRepository,
)
from app.domain.shared import UnitOfWork


def _build_alert_context(connection: CloudConnection, alarm: AlarmState) -> dict:
    description = f"CloudWatch alarm '{alarm.name}' is in ALARM state"
    if alarm.reason:
        description += f": {alarm.reason}"
    context: dict = {"service": connection.project, "alert": description}
    if alarm.metric_name:
        context["metrics"] = {"namespace": alarm.namespace, "metric_name": alarm.metric_name}
    return context


@dataclass
class PollAlarmsJob:
    connections: CloudConnectionRepository
    tracked: TrackedAlarmRepository
    fetcher: AlarmFetcher
    ingest: IngestIncident
    resolve: ResolveIncident
    uow: UnitOfWork

    async def run(self) -> None:
        for connection in await self.connections.list():
            try:
                alarms = await self.fetcher.list_alarms(connection)
            except Exception as exc:  # noqa: BLE001 - isolate this connection's failure, keep polling others
                await self.connections.record_poll_result(
                    connection.id, status="error", error=str(exc)
                )
                await self.uow.commit()
                continue

            for alarm in alarms:
                await self._apply(connection, alarm)

            await self.connections.record_poll_result(connection.id, status="ok", error=None)
            await self.uow.commit()

    async def _apply(self, connection: CloudConnection, alarm: AlarmState) -> None:
        existing = await self.tracked.get(connection.id, alarm.arn)
        was_alarming = existing is not None and existing.last_state == "ALARM"

        if alarm.state == "ALARM" and not was_alarming:
            incident, _ = await self.ingest.execute(
                source="cloudwatch_alarm", context=_build_alert_context(connection, alarm)
            )
            await self.tracked.upsert(
                connection.id, alarm_arn=alarm.arn, alarm_name=alarm.name,
                last_state="ALARM", incident_id=incident.id,
            )
        elif alarm.state == "OK" and was_alarming:
            if existing.incident_id is not None:
                await self.resolve.resolve(
                    existing.incident_id,
                    resolution_notes=f"Auto-resolved: alarm '{alarm.name}' returned to OK",
                )
            await self.tracked.upsert(
                connection.id, alarm_arn=alarm.arn, alarm_name=alarm.name,
                last_state="OK", incident_id=None,
            )
