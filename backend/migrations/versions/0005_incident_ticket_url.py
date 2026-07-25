"""add incidents.ticket_url

Revision ID: 0005_incident_ticket_url
Revises: 0004_known_issue_matching
Create Date: 2026-07-25

Nullable column recording the ticket URL created for an incident (feature: one-click Azure
DevOps ticket creation for a genuinely new/unseen error).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_incident_ticket_url"
down_revision: Union[str, None] = "0004_known_issue_matching"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("ticket_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "ticket_url")
