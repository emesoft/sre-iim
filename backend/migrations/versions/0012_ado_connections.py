"""add ado_connections

Revision ID: 0012_ado_connections
Revises: 0011_chat
Create Date: 2026-08-23

Per-project Azure DevOps ticket config, replacing the global AZDO_ORG/AZDO_PROJECT/AZDO_PAT env
vars — each internal project (EVP, rxdevs, ...) can file tickets into its own ADO org/project.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_ado_connections"
down_revision: Union[str, None] = "0011_chat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ado_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project", sa.Text(), nullable=False, unique=True),
        sa.Column("org", sa.Text(), nullable=False),
        sa.Column("ado_project", sa.Text(), nullable=False),
        sa.Column("encrypted_pat", sa.Text(), nullable=False),
        sa.Column("work_item_type", sa.Text(), nullable=False, server_default="Bug"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("ado_connections")
