"""Unit tests for PollAlarmsJob: the ALARM<->OK state machine, using in-memory fakes.

Covers: OK/untracked -> ALARM creates an incident (status="new", no analysis triggered);
ALARM -> ALARM is a no-op (no duplicate incident); ALARM -> OK auto-resolves; one integration's
fetch/apply error doesn't stop the others; a paused integration is skipped by the scheduled sweep
but still polled by an explicit refresh.
"""

import uuid

import pytest

from app.application.integrations.poll_alarms import ConnectionPollOutcome, PollAlarmsJob
from app.domain.integrations.entities import ALARMS, AlarmState, Integration, TrackedAlarm
from app.interface.http.dto.mappers.incident import build_headline

pytestmark = pytest.mark.asyncio


class FakeIntegrationRepo:
    def __init__(self, integrations):
        self._rows = {c.id: c for c in integrations}
        self.poll_results = []

    async def get(self, integration_id):
        return self._rows.get(integration_id)

    async def list(self, *, capability=None, enabled_only=False):
        return [
            i
            for i in self._rows.values()
            if (capability is None or i.supports(capability)) and (not enabled_only or i.enabled)
        ]

    async def record_run(self, integration_id, capability, *, status, error, item_count=None):
        self.poll_results.append((integration_id, status, error, item_count))


class FakeTrackedAlarmRepo:
    def __init__(self, seed=None):
        self._rows: dict[tuple, TrackedAlarm] = seed or {}

    async def get(self, connection_id, alarm_arn):
        return self._rows.get((connection_id, alarm_arn))

    async def upsert(
        self, connection_id, *, alarm_arn, alarm_name, last_state, incident_id,
        last_incident_id=None, occurrence_count=None,
    ):
        existing = self._rows.get((connection_id, alarm_arn))
        self._rows[(connection_id, alarm_arn)] = TrackedAlarm(
            connection_id=connection_id, alarm_arn=alarm_arn, alarm_name=alarm_name,
            last_state=last_state, incident_id=incident_id,
            last_incident_id=(
                last_incident_id if last_incident_id is not None
                else (existing.last_incident_id if existing else None)
            ),
            occurrence_count=(
                occurrence_count if occurrence_count is not None
                else (existing.occurrence_count if existing else 0)
            ),
        )


class FakeAlarmSource:
    def __init__(self, by_integration):
        self._by_integration = by_integration

    async def list_alarms(self, integration):
        result = self._by_integration[integration.id]
        if isinstance(result, Exception):
            raise result
        return result


class FakeRegistry:
    """Stands in for ProviderRegistry: one alarm source for every provider, so these tests are
    about the state machine rather than about provider routing (that's test_provider_registry)."""

    def __init__(self, by_integration):
        self._source = FakeAlarmSource(by_integration)

    def alarm_source(self, provider):
        return self._source


class FakeIngest:
    """PollAlarmsJob only ever calls `create_incident` (no analysis) — see the module docstring:
    alarm-created incidents wait for a manual "Analyze with AI" trigger."""

    def __init__(self):
        self.calls = []

    async def create_incident(
        self, *, source, context, status="analyzing", occurrence_count=1, previous_incident_id=None
    ):
        self.calls.append((source, context, status, occurrence_count, previous_incident_id))
        incident = type("I", (), {"id": uuid.uuid4()})()
        return incident


class FakeResolve:
    def __init__(self, raises: Exception | None = None):
        self.calls = []
        self._raises = raises

    async def resolve(self, incident_id, *, resolution_notes):
        self.calls.append((incident_id, resolution_notes))
        if self._raises is not None:
            raise self._raises


class FakeUnitOfWork:
    async def commit(self):
        pass

    async def rollback(self):
        pass


def _connection(cloud="aws", enabled=True, **kw):
    return Integration(
        id=uuid.uuid4(), project="GCM", env="prod", provider=cloud, enabled=enabled,
        config={"region": "ap-southeast-1", "auth_type": "sso", "sso_profile_name": "p"},
        capabilities=(ALARMS,), **kw,
    )


async def test_new_alarm_creates_an_incident_without_analyzing_it():
    conn = _connection()
    alarms_by_integration = ({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")]})
    tracked = FakeTrackedAlarmRepo()
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([conn]), tracked=tracked, registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    assert len(ingest.calls) == 1
    assert ingest.calls[0][0] == "cloudwatch_alarm"
    assert ingest.calls[0][1]["env"] == conn.env
    assert ingest.calls[0][2] == "new"  # status — no auto-analysis
    assert (await tracked.get(conn.id, "arn:1")).last_state == "ALARM"


async def test_newrelic_connection_creates_an_incident_with_newrelic_wording():
    """Regression: PollAlarmsJob used to hard-code "CloudWatch alarm" wording and
    source="cloudwatch_alarm" for every connection, so a New Relic-polled incident's alert text
    and source badge both lied about where it came from."""
    conn = _connection(cloud="newrelic")
    alarms_by_integration = (
        {conn.id: [AlarmState(arn="issue:1", name="CRITICAL - SES Errors", state="ALARM", reason="GCM-SES-Alerts")]}
    )
    tracked = FakeTrackedAlarmRepo()
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([conn]), tracked=tracked, registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    assert ingest.calls[0][0] == "newrelic_issue"
    alert = ingest.calls[0][1]["alert"]
    assert "CloudWatch" not in alert
    assert "New Relic" in alert
    assert "CRITICAL - SES Errors" in alert
    assert "GCM-SES-Alerts" in alert


async def test_newrelic_incident_with_detail_includes_the_fuller_sentence():
    """`alarm.detail` (New Relic's fuller title) gets appended after the always-quoted `alarm.name`
    anchor. The name can end up mentioned twice in the full sentence (once as the anchor, once
    inside `detail`'s own quoting) — accepted, because the alternative (dropping the anchor quotes
    whenever detail is present) previously broke incident-list headline extraction for issues whose
    `detail` doesn't happen to quote the name at all (see build_headline in dto/mappers/incident.py:
    it extracts the first 'quoted' substring, so that quoting must never be conditional)."""
    conn = _connection(cloud="newrelic")
    alarms_by_integration = (
        {
            conn.id: [
                AlarmState(
                    arn="issue:1",
                    name="CRITICAL - Yelp Errors",
                    state="ALARM",
                    reason="GCM-Yelp-Alerts",
                    detail="Log query result is > 0.0 on 'CRITICAL - Yelp Errors'",
                )
            ]
        }
    )
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([conn]), tracked=FakeTrackedAlarmRepo(), registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    alert = ingest.calls[0][1]["alert"]
    assert alert == (
        "New Relic alert 'CRITICAL - Yelp Errors': Log query result is > 0.0 on "
        "'CRITICAL - Yelp Errors' (policy: GCM-Yelp-Alerts)"
    )
    # The invariant that actually matters: the name is still the FIRST quoted substring, so
    # build_headline's extraction is correct regardless of what detail says afterward.
    assert build_headline({"alert": alert}) == "CRITICAL - Yelp Errors"


async def test_newrelic_incident_without_detail_still_quotes_the_name_for_headline_extraction():
    """Regression: an issue whose fuller title doesn't happen to quote the name at all (e.g. New
    Relic's simpler "X Status Issue Found" alert types, as opposed to the templated "Log query
    result is > 0.0 on 'X'" ones) must not lose its quoting — a version of this code dropped the
    anchor quotes whenever `alarm.detail` was set, which made build_headline fall back to the
    entire raw sentence as the incident's title for exactly this case."""
    conn = _connection(cloud="newrelic")
    alarms_by_integration = (
        {
            conn.id: [
                AlarmState(
                    arn="issue:1",
                    name="Sendgrid Status Issue Found",
                    state="ALARM",
                    reason="gcm-alerts-email",
                    detail="Sendgrid Status Issue Found",  # no quotes anywhere in this text
                )
            ]
        }
    )
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([conn]), tracked=FakeTrackedAlarmRepo(), registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    alert = ingest.calls[0][1]["alert"]
    assert build_headline({"alert": alert}) == "Sendgrid Status Issue Found"


async def test_already_alarming_does_not_create_a_second_incident():
    conn = _connection()
    existing_incident_id = uuid.uuid4()
    tracked = FakeTrackedAlarmRepo(
        seed={(conn.id, "arn:1"): TrackedAlarm(
            connection_id=conn.id, alarm_arn="arn:1", alarm_name="cpu-high",
            last_state="ALARM", incident_id=existing_incident_id,
        )}
    )
    alarms_by_integration = ({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")]})
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([conn]), tracked=tracked, registry=FakeRegistry(alarms_by_integration),
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
    alarms_by_integration = ({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="OK")]})
    resolve = FakeResolve()
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([conn]), tracked=tracked, registry=FakeRegistry(alarms_by_integration),
        ingest=FakeIngest(), resolve=resolve, uow=FakeUnitOfWork(),
    )
    await job.run()
    assert resolve.calls == [(incident_id, resolve.calls[0][1])]
    assert (await tracked.get(conn.id, "arn:1")).last_state == "OK"
    assert (await tracked.get(conn.id, "arn:1")).incident_id is None


async def test_recovered_alarm_calls_resolve_even_when_the_incident_was_never_analyzed():
    """An alarm-created incident starts unanalyzed (status="new"). ResolveIncident.resolve()
    itself now handles "nothing to write up as a known-issue case" by just closing the incident
    out (see test_resolve_usecase.py) — the poller only needs to call it, not special-case the
    outcome."""
    conn = _connection()
    incident_id = uuid.uuid4()
    tracked = FakeTrackedAlarmRepo(
        seed={(conn.id, "arn:1"): TrackedAlarm(
            connection_id=conn.id, alarm_arn="arn:1", alarm_name="cpu-high",
            last_state="ALARM", incident_id=incident_id,
        )}
    )
    alarms_by_integration = ({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="OK")]})
    resolve = FakeResolve()
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([conn]), tracked=tracked, registry=FakeRegistry(alarms_by_integration),
        ingest=FakeIngest(), resolve=resolve, uow=FakeUnitOfWork(),
    )
    outcomes = await job.run()
    assert resolve.calls == [(incident_id, resolve.calls[0][1])]
    assert (await tracked.get(conn.id, "arn:1")).last_state == "OK"
    assert (await tracked.get(conn.id, "arn:1")).incident_id is None
    assert outcomes[0].status == "ok"  # not treated as a poll error


async def test_one_connection_error_does_not_stop_the_others():
    good = _connection()
    bad = _connection()
    alarms_by_integration = ({
        good.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")],
        bad.id: RuntimeError("SSO token expired"),
    })
    connections = FakeIntegrationRepo([bad, good])
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=connections, tracked=FakeTrackedAlarmRepo(), registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    outcomes = await job.run()
    assert len(ingest.calls) == 1  # good connection still processed
    assert (bad.id, "error", "SSO token expired", None) in connections.poll_results
    assert any(
        cid == good.id and status == "ok" for cid, status, _, _ in connections.poll_results
    )
    assert {(o.connection_id, o.status, o.alarm_count) for o in outcomes} == {
        (bad.id, "error", 0),
        (good.id, "ok", 1),
    }


async def test_resolve_failure_for_one_connection_does_not_stop_the_others():
    """Important 3: a failure applying one alarm (e.g. ResolveIncident raising) must be caught and
    recorded, not abort the whole run() call before later connections are polled."""
    bad = _connection()
    bad_incident_id = uuid.uuid4()
    good = _connection()

    tracked = FakeTrackedAlarmRepo(
        seed={
            (bad.id, "arn:1"): TrackedAlarm(
                connection_id=bad.id, alarm_arn="arn:1", alarm_name="cpu-high",
                last_state="ALARM", incident_id=bad_incident_id,
            )
        }
    )
    alarms_by_integration = ({
        bad.id: [AlarmState(arn="arn:1", name="cpu-high", state="OK")],
        good.id: [AlarmState(arn="arn:2", name="mem-high", state="ALARM")],
    })
    connections = FakeIntegrationRepo([bad, good])
    ingest = FakeIngest()
    resolve = FakeResolve(raises=RuntimeError("no analysis to resolve"))
    job = PollAlarmsJob(
        integrations=connections, tracked=tracked, registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=resolve, uow=FakeUnitOfWork(),
    )
    await job.run()

    # The bad connection's failure is recorded...
    assert (bad.id, "error", "no analysis to resolve", None) in connections.poll_results
    # ...and the good connection is still polled and creates its incident.
    assert len(ingest.calls) == 1
    assert any(
        cid == good.id and status == "ok" for cid, status, _, _ in connections.poll_results
    )


async def test_run_with_a_connection_id_only_polls_that_connection():
    """The per-connection Refresh button (POST /api/cloud-connections/{id}/poll) must not touch
    other connections, even if they'd otherwise create/resolve incidents."""
    target = _connection()
    other = _connection()
    alarms_by_integration = ({
        target.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")],
        other.id: [AlarmState(arn="arn:2", name="mem-high", state="ALARM")],
    })
    connections = FakeIntegrationRepo([target, other])
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=connections, tracked=FakeTrackedAlarmRepo(), registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    outcomes = await job.run(connection_id=target.id)
    assert len(ingest.calls) == 1
    assert connections.poll_results == [(target.id, "ok", None, 1)]
    assert outcomes == [ConnectionPollOutcome(target.id, status="ok", error=None, alarm_count=1)]


async def test_run_with_an_unknown_connection_id_is_a_noop():
    connections = FakeIntegrationRepo([])
    job = PollAlarmsJob(
        integrations=connections, tracked=FakeTrackedAlarmRepo(), registry=FakeRegistry({}),
        ingest=FakeIngest(), resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    outcomes = await job.run(connection_id=uuid.uuid4())
    assert connections.poll_results == []
    assert outcomes == []


async def test_first_time_alarm_has_occurrence_count_one_and_no_previous_incident():
    conn = _connection()
    alarms_by_integration = ({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")]})
    tracked = FakeTrackedAlarmRepo()
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([conn]), tracked=tracked, registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    _, _, _, occurrence_count, previous_incident_id = ingest.calls[0]
    assert occurrence_count == 1
    assert previous_incident_id is None
    row = await tracked.get(conn.id, "arn:1")
    assert row.occurrence_count == 1
    assert row.last_incident_id == row.incident_id


async def test_recurring_alarm_links_back_to_the_resolved_incident_and_counts_up():
    conn = _connection()
    first_incident_id = uuid.uuid4()
    tracked = FakeTrackedAlarmRepo(
        seed={
            (conn.id, "arn:1"): TrackedAlarm(
                connection_id=conn.id, alarm_arn="arn:1", alarm_name="cpu-high",
                last_state="OK", incident_id=None,
                last_incident_id=first_incident_id, occurrence_count=1,
            )
        }
    )
    alarms_by_integration = ({conn.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")]})
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([conn]), tracked=tracked, registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    await job.run()
    _, _, _, occurrence_count, previous_incident_id = ingest.calls[0]
    assert occurrence_count == 2
    assert previous_incident_id == first_incident_id
    row = await tracked.get(conn.id, "arn:1")
    assert row.occurrence_count == 2
    assert row.last_incident_id == row.incident_id  # now points at the new incident


async def test_disabled_connection_is_skipped_by_the_scheduled_sweep():
    disabled = _connection(enabled=False)
    enabled = _connection()
    alarms_by_integration = ({
        disabled.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")],
        enabled.id: [AlarmState(arn="arn:2", name="mem-high", state="ALARM")],
    })
    connections = FakeIntegrationRepo([disabled, enabled])
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=connections, tracked=FakeTrackedAlarmRepo(), registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    outcomes = await job.run()
    assert len(ingest.calls) == 1  # only the enabled connection's alarm became an incident
    assert outcomes == [ConnectionPollOutcome(enabled.id, status="ok", error=None, alarm_count=1)]
    assert connections.poll_results == [(enabled.id, "ok", None, 1)]


async def test_a_disabled_connection_can_still_be_manually_refreshed():
    """An explicit "Refresh" click (run(connection_id=...)) bypasses `enabled` — pausing a
    connection stops the unattended sweep, not a user's own action on it."""
    disabled = _connection(enabled=False)
    alarms_by_integration = ({disabled.id: [AlarmState(arn="arn:1", name="cpu-high", state="ALARM")]})
    ingest = FakeIngest()
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([disabled]), tracked=FakeTrackedAlarmRepo(), registry=FakeRegistry(alarms_by_integration),
        ingest=ingest, resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    outcomes = await job.run(connection_id=disabled.id)
    assert len(ingest.calls) == 1
    assert outcomes == [ConnectionPollOutcome(disabled.id, status="ok", error=None, alarm_count=1)]


async def test_multiple_alarming_alarms_are_all_counted():
    conn = _connection()
    alarms_by_integration = ({
        conn.id: [
            AlarmState(arn="arn:1", name="cpu-high", state="ALARM"),
            AlarmState(arn="arn:2", name="mem-high", state="ALARM"),
            AlarmState(arn="arn:3", name="disk-ok", state="OK"),
        ]
    })
    job = PollAlarmsJob(
        integrations=FakeIntegrationRepo([conn]), tracked=FakeTrackedAlarmRepo(), registry=FakeRegistry(alarms_by_integration),
        ingest=FakeIngest(), resolve=FakeResolve(), uow=FakeUnitOfWork(),
    )
    outcomes = await job.run()
    assert outcomes == [ConnectionPollOutcome(conn.id, status="ok", error=None, alarm_count=2)]
