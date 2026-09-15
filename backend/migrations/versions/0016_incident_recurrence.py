"""track alarm recurrence: occurrence_count + previous_incident_id

Revision ID: 0016_incident_recurrence
Revises: 0015_users_username
Create Date: 2026-09-11

A CloudWatch alarm that fires, resolves, then fires again currently opens a brand-new incident
with no link to the one it closed — the UI has no way to show "this has recurred N times". Adds:
- incidents.occurrence_count (default 1) + incidents.previous_incident_id — set once at creation
  by PollAlarmsJob for alarm-sourced incidents; every other incident stays at the default (1, NULL).
- tracked_alarms.last_incident_id + tracked_alarms.occurrence_count — unlike tracked_alarms's
  existing `incident_id` (cleared to NULL when the alarm resolves), these are never cleared, so the
  next OK->ALARM transition for the same alarm ARN can still find the previous incident and count.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_incident_recurrence"
down_revision: Union[str, None] = "0015_users_username"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "incidents",
        sa.Column("previous_incident_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_incidents_previous_incident_id",
        "incidents",
        "incidents",
        ["previous_incident_id"],
        ["id"],
    )

    op.add_column(
        "tracked_alarms",
        sa.Column("last_incident_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "tracked_alarms",
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_foreign_key(
        "fk_tracked_alarms_last_incident_id",
        "tracked_alarms",
        "incidents",
        ["last_incident_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_tracked_alarms_last_incident_id", "tracked_alarms", type_="foreignkey")
    op.drop_column("tracked_alarms", "occurrence_count")
    op.drop_column("tracked_alarms", "last_incident_id")

    op.drop_constraint("fk_incidents_previous_incident_id", "incidents", type_="foreignkey")
    op.drop_column("incidents", "previous_incident_id")
    op.drop_column("incidents", "occurrence_count")
