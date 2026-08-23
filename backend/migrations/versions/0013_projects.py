"""add projects registry, FK from cloud_connections/ado_connections.project

Revision ID: 0013_projects
Revises: 0012_ado_connections
Create Date: 2026-08-23

A shared registry of project names (`.claude/specs/2026-08-23-project-registry-design.md`).
`cloud_connections.project`/`ado_connections.project` stay TEXT columns (avoids reworking every
existing string-based lookup, e.g. `AdoConnectionRepository.get_by_project(incident.service)`) but
gain a database-level FK to `projects.name` so a typo or unregistered project name can no longer be
saved. The backfill runs before the FK is added so existing rows (EVP, rxdevs, ...) don't violate
the new constraint.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_projects"
down_revision: Union[str, None] = "0012_ado_connections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.execute(
        "INSERT INTO projects (id, name, created_at) "
        "SELECT gen_random_uuid(), distinct_project.name, now() FROM ("
        "  SELECT DISTINCT project AS name FROM cloud_connections "
        "  UNION "
        "  SELECT DISTINCT project AS name FROM ado_connections"
        ") AS distinct_project"
    )
    op.create_foreign_key(
        "fk_cloud_connections_project_projects",
        "cloud_connections", "projects",
        ["project"], ["name"],
        onupdate="CASCADE", ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ado_connections_project_projects",
        "ado_connections", "projects",
        ["project"], ["name"],
        onupdate="CASCADE", ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_ado_connections_project_projects", "ado_connections", type_="foreignkey")
    op.drop_constraint("fk_cloud_connections_project_projects", "cloud_connections", type_="foreignkey")
    op.drop_table("projects")
