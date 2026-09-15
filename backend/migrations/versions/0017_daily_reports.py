"""persist generated daily reports

Revision ID: 0017_daily_reports
Revises: 0016_incident_recurrence
Create Date: 2026-09-11

Adds `daily_reports`, keyed by (report_date, service), so a generated digest is computed once per
day per project scope and reloaded on later visits instead of re-running the LLM every time the
Reports page is opened. `service = ''` is the sentinel for "All projects" (a real project name is
never empty) — kept as a plain non-null string rather than nullable so the unique constraint
actually dedupes that scope too (NULL is never equal to NULL in SQL).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_daily_reports"
down_revision: Union[str, None] = "0016_incident_recurrence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "counts_by_severity", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("counts_by_status", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("incidents", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("slack_markdown", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("report_date", "service", name="uq_daily_reports_date_service"),
    )
    op.create_index("ix_daily_reports_report_date", "daily_reports", ["report_date"])


def downgrade() -> None:
    op.drop_index("ix_daily_reports_report_date", table_name="daily_reports")
    op.drop_table("daily_reports")
