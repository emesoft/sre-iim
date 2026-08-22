"""Unit tests for PollAlarmsJob: the ALARM<->OK state machine, using in-memory fakes.

Covers: OK/untracked -> ALARM creates an incident; ALARM -> ALARM is a no-op (no duplicate
incident); ALARM -> OK auto-resolves; one connection's fetch error doesn't stop the others.
"""

import uuid

import pytest

from app.application.cloud_connections.poll_alarms import PollAlarmsJob
from app.domain.cloud_connections.entities import AlarmState, CloudConnection, TrackedAlarm

pytestmark = pytest.mark.asyncio


class FakeConnectionRepo:
    def __init__(self, connections):
        self._connections = {c.id: c for c in connections}
        self.poll_results = []

    async def list(self):
        return list(self._connections.values())

    async def record_poll_result(self, connection_id, *, status, error):
        self.poll_results.append((connection_id, status, error))


class FakeTrackedAlarmRepo:
    def __init__(self, seed=None):
        self._rows: dict[tuple, TrackedAlarm] = seed or {}

    async def get(self, connection_id, alarm_arn):
        return self._rows.get((connection_id, alarm_arn))

    async def upsert(self, connection_id, *, alarm_arn, alarm_name, last_state, incident_id):
        self._rows[(connection_id, alarm_arn)] = TrackedAlarm(
            connection_id=connection_id, alarm_arn=alarm_arn, alarm_name=alarm_name,
            last_state=last_state, incident_id=incident_id,
        )


class FakeFetcher:
    def __init__(self, by_connection):
        self._by_connection = by_connection

    async def list_alarms(self, connection):
        result = self._by_connection[connection.id]
        if isinstance(result, Exception):
            raise result
        return result


class FakeIngest:
    def __init__(self):
        self.calls = []

    async def execute(self, *, source, context):
        self.calls.append((source, context))
        incident = type("I", (), {"id": uuid.uuid4()})()
        return incident, None


class FakeResolve:
    def __init__(self):
        self.calls = []

    async def resolve(self, incident_id, *, resolution_notes):
        self.calls.append((incident_id, resolution_notes))


class FakeUnitOfWork:
    async def commit(self):
        pass


def _connection(**kw):
    return CloudConnection(
        id=uuid.uuid4(), project="GCM", env="prod", region="ap-southeast-1", auth_type="sso",
        sso_profile_name="p", **kw,
    )


async def test_new_alarm_creates_an_incident():
    conn = _connection()
    fetcher = FakeFetcher({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")]})
    tracked = FakeTrackedAlarmRepo()
    ingest = FakeIngest()
    job = PollAlarmsJob(
        connections=FakeConnectionRepo([conn]), tracked=tracked, fetcher=fetcher,
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    assert len(ingest.calls) == 1
    assert ingest.calls[0][0] == "cloudwatch_alarm"
    assert (await tracked.get(conn.id, "arn:1")).last_state == "ALARM"


async def test_already_alarming_does_not_create_a_second_incident():
    conn = _connection()
    existing_incident_id = uuid.uuid4()
    tracked = FakeTrackedAlarmRepo(
        seed={(conn.id, "arn:1"): TrackedAlarm(
            connection_id=conn.id, alarm_arn="arn:1", alarm_name="cpu-high",
            last_state="ALARM", incident_id=existing_incident_id,
        )}
    )
    fetcher = FakeFetcher({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")]})
    ingest = FakeIngest()
    job = PollAlarmsJob(
        connections=FakeConnectionRepo([conn]), tracked=tracked, fetcher=fetcher,
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    assert ingest.calls == []


async def test_recovered_alarm_auto_resolves_the_incident():
    conn = _connection()
    incident_id = uuid.uuid4()
    tracked = FakeTrackedAlarmRepo(
        seed={(conn.id, "arn:1"): TrackedAlarm(
            connection_id=conn.id, alarm_arn="arn:1", alarm_name="cpu-high",
            last_state="ALARM", incident_id=incident_id,
        )}
    )
    fetcher = FakeFetcher({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="OK")]})
    resolve = FakeResolve()
    job = PollAlarmsJob(
        connections=FakeConnectionRepo([conn]), tracked=tracked, fetcher=fetcher,
        ingest=FakeIngest(), resolve=resolve, uow=FakeUnitOfWork(),
    )
    await job.run()
    assert resolve.calls == [(incident_id, resolve.calls[0][1])]
    assert (await tracked.get(conn.id, "arn:1")).last_state == "OK"
    assert (await tracked.get(conn.id, "arn:1")).incident_id is None


async def test_one_connection_error_does_not_stop_the_others():
    good = _connection()
    bad = _connection()
    fetcher = FakeFetcher({
        good.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")],
        bad.id: RuntimeError("SSO token expired"),
    })
    connections = FakeConnectionRepo([bad, good])
    ingest = FakeIngest()
    job = PollAlarmsJob(
        connections=connections, tracked=FakeTrackedAlarmRepo(), fetcher=fetcher,
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    assert len(ingest.calls) == 1  # good connection still processed
    assert (bad.id, "error", "SSO token expired") in connections.poll_results
    assert any(cid == good.id and status == "ok" for cid, status, _ in connections.poll_results)
