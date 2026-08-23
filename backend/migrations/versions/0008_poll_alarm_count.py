"""add last_poll_alarm_count to cloud_connections

Revision ID: 0008_poll_alarm_count
Revises: 0007_app_settings
Create Date: 2026-08-22

Records how many alarms were seen in ALARM state during the most recent poll of a connection, so
the Settings page can show "2 alarms active" next to the last-poll status instead of just ok/error.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_poll_alarm_count"
down_revision: Union[str, None] = "0007_app_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cloud_connections", sa.Column("last_poll_alarm_count", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("cloud_connections", "last_poll_alarm_count")
