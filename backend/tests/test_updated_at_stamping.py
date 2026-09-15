"""Unit tests that `updated_at` actually tracks updates — no DB, compiled SQL only.

This is load-bearing for the stuck-analysis sweep (`_requeue_interrupted` in main.py), which asks
"has this row claimed to be analyzing for longer than the threshold?" and can only answer that if
the column moves when the status does. Before this, `updated_at` was stamped once at insert and
never again: it silently meant `created_at`, so every incident older than the threshold looked
stale the instant someone pressed Analyze — and the sweep would have requeued a run that was
happily in progress, paying for it twice.
"""

from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.dialects import postgresql

from app.infrastructure.db.orm import DocumentRow, IncidentRow


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_changing_an_incidents_status_restamps_updated_at():
    sql = _sql(update(IncidentRow).values(status="new"))
    assert "updated_at=now()" in sql


def test_created_at_is_never_restamped():
    """The pair is only useful if one of them stays put."""
    sql = _sql(update(IncidentRow).values(status="new"))
    assert "created_at" not in sql


def test_documents_track_their_own_edits_too():
    """The Knowledge Base shows "updated" per document; a frozen column would quietly lie."""
    assert "updated_at=now()" in _sql(update(DocumentRow).values(title="x"))


def test_the_staleness_filter_reads_the_column_that_moves():
    """Pins the two halves together: the sweep filters on `updated_at`, so `updated_at` is the one
    that must be re-stamped. Swapping it for `created_at` would compile fine and be wrong."""
    stmt = (
        update(IncidentRow)
        .where(
            IncidentRow.status == "analyzing",
            IncidentRow.updated_at < datetime.now(timezone.utc),
        )
        .values(status="new")
    )
    sql = _sql(stmt)
    assert "incidents.updated_at <" in sql
    assert "updated_at=now()" in sql
