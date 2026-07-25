"""known-issue matching: documents.incident_id, analyses.known_issue_*

Revision ID: 0004_known_issue_matching
Revises: 0003_incident_log_group
Create Date: 2026-07-25

Resolved incidents are saved as `documents` (source_type="incident") so future similar incidents
surface them via the existing RAG retrieval path. `documents.incident_id` links a case write-up
back to its source incident; `analyses.known_issue_incident_id`/`known_issue_similarity` record
which past incident (if any) a new analysis matched, above the known-issue similarity threshold.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_known_issue_matching"
down_revision: Union[str, None] = "0003_incident_log_group"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_documents_incident_id", "documents", "incidents", ["incident_id"], ["id"]
    )
    op.add_column(
        "analyses",
        sa.Column("known_issue_incident_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_analyses_known_issue_incident_id",
        "analyses",
        "incidents",
        ["known_issue_incident_id"],
        ["id"],
    )
    op.add_column("analyses", sa.Column("known_issue_similarity", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("analyses", "known_issue_similarity")
    op.drop_constraint("fk_analyses_known_issue_incident_id", "analyses", type_="foreignkey")
    op.drop_column("analyses", "known_issue_incident_id")
    op.drop_constraint("fk_documents_incident_id", "documents", type_="foreignkey")
    op.drop_column("documents", "incident_id")
