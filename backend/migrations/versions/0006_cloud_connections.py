"""add cloud_connections and tracked_alarms

Revision ID: 0006_cloud_connections
Revises: 0005_incident_ticket_url
Create Date: 2026-08-22

CloudWatch alarm polling: per-account connection config (SSO profile or encrypted access key) and
the last-seen state of each polled alarm, so the poller can tell OK->ALARM from ALARM->ALARM.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_cloud_connections"
down_revision: Union[str, None] = "0005_incident_ticket_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cloud_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("env", sa.Text(), nullable=False),
        sa.Column("cloud", sa.Text(), nullable=False, server_default="aws"),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.Text(), nullable=False),  # sso | access_key
        sa.Column("sso_profile_name", sa.Text(), nullable=True),
        sa.Column("encrypted_access_key_id", sa.Text(), nullable=True),
        sa.Column("encrypted_secret_access_key", sa.Text(), nullable=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_poll_status", sa.Text(), nullable=True),  # ok | error
        sa.Column("last_poll_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "tracked_alarms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cloud_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alarm_arn", sa.Text(), nullable=False),
        sa.Column("alarm_name", sa.Text(), nullable=False),
        sa.Column("last_state", sa.Text(), nullable=False),  # OK | ALARM
        sa.Column(
            "incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id"), nullable=True
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_tracked_alarms_connection_id", "tracked_alarms", ["connection_id"])
    op.create_unique_constraint(
        "uq_tracked_alarms_connection_arn", "tracked_alarms", ["connection_id", "alarm_arn"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_tracked_alarms_connection_arn", "tracked_alarms", type_="unique")
    op.drop_index("ix_tracked_alarms_connection_id", table_name="tracked_alarms")
    op.drop_table("tracked_alarms")
    op.drop_table("cloud_connections")
