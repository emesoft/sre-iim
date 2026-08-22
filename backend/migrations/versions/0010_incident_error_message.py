"""add error_message to incidents

Revision ID: 0010_incident_error_message
Revises: 0009_analysis_tokens
Create Date: 2026-08-22

The analysis-failure reason was only ever published transiently over SSE — a viewer who wasn't
watching live (or reloaded the page) saw just the generic "Analysis failed" message with no way to
find out why. Persisting it lets the incident detail show the real cause on any later load.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_incident_error_message"
down_revision: Union[str, None] = "0009_analysis_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "error_message")
