"""add per-project auto_analyze switch

Revision ID: 0020_project_auto_analyze
Revises: 0019_integrations
Create Date: 2026-09-12

Automatic analysis spends money per incident, and not every project wants that on. The global
`AUTO_ANALYZE_*` settings are all-or-nothing; this is the per-project switch, so one noisy or
low-value project can be paused without stopping triage for the rest.

Defaults to true: a project that has said nothing keeps the behaviour the feature shipped with.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_project_auto_analyze"
down_revision: Union[str, None] = "0019_integrations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("auto_analyze", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("projects", "auto_analyze")
