"""add app_settings table

Revision ID: 0007_app_settings
Revises: 0006_cloud_connections
Create Date: 2026-08-22

Generic encrypted key-value store for app-wide secrets that don't fit the per-project
cloud_connections shape — first use: the Claude Code headless OAuth token entered on the
Settings page (feature: local-demo Claude CLI LLM provider).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_app_settings"
down_revision: Union[str, None] = "0006_cloud_connections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
