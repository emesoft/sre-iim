"""add enabled to cloud_connections

Revision ID: 0018_cloud_connection_enabled
Revises: 0017_daily_reports
Create Date: 2026-09-11

Lets a connection be paused (excluded from the scheduled poll) without deleting it — e.g. while
its credentials are being fixed, to stop a broken connection from spamming last_poll_error on
every scheduler tick. The manual per-connection "Refresh"/"Test" actions still work regardless of
this flag; only the global scheduled poll (PollAlarmsJob.run() with no connection_id) skips
disabled connections.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_cloud_connection_enabled"
down_revision: Union[str, None] = "0017_daily_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cloud_connections",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("cloud_connections", "enabled")
