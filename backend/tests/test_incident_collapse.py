"""Unit tests for "how many incidents is this, really" — no DB, compiled SQL only.

A recurring alarm chains each firing to the last, and every list shows only the newest link: five
firings of one alarm are one thing to work on. The counts have to agree, and once didn't — a
"Resolved 19" tab sat above nine visible rows, because the tab counted raw rows and the table
counted chains. One predicate now serves both; these pin that it is actually applied to each.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects import postgresql

from app.infrastructure.db.repositories.incidents import (
    SqlAlchemyIncidentRepository,
    _not_superseded,
)


class _CapturingSession:
    """Captures the statement instead of executing it — enough to assert on the SQL built."""

    def __init__(self):
        self.statements = []

    async def execute(self, stmt, *a, **kw):
        self.statements.append(stmt)
        raise _Stop()


class _Stop(Exception):
    pass


def _sql_of(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_the_predicate_excludes_rows_another_incident_supersedes():
    from sqlalchemy import select

    from app.infrastructure.db.orm import IncidentRow

    sql = _sql_of(select(IncidentRow.id).where(_not_superseded()))
    assert "NOT (EXISTS" in sql
    assert "previous_incident_id" in sql


async def test_the_incident_list_applies_it():
    session = _CapturingSession()
    try:
        await SqlAlchemyIncidentRepository(session).list(limit=10)
    except _Stop:
        pass
    assert "previous_incident_id" in _sql_of(session.statements[0])


async def test_the_rollup_applies_it_too():
    """The half that was wrong. If this ever stops being true the tabs start disagreeing with the
    rows again, and nothing else would notice."""
    session = _CapturingSession()
    try:
        await SqlAlchemyIncidentRepository(session).rollup(
            since=datetime.now(timezone.utc) - timedelta(hours=24)
        )
    except _Stop:
        pass
    assert "previous_incident_id" in _sql_of(session.statements[0])
