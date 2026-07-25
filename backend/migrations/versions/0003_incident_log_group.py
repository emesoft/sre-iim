"""add incidents.log_group

Revision ID: 0003_incident_log_group
Revises: 0002_embedding_dim
Create Date: 2026-07-25

Nullable column recording the CloudWatch log group an incident's logs were last searched against
(feature: on-demand CloudWatch Logs Insights search from the incident detail page).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_incident_log_group"
down_revision: Union[str, None] = "0002_embedding_dim"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("log_group", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "log_group")
