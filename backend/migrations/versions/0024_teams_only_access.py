"""drop direct user->project grants; access comes from teams only

Revision ID: 0024_teams_only_access
Revises: 0023_teams
Create Date: 2026-09-12

Two ways to grant the same thing turned out to be one too many. The admin screen showed a user's
projects from both sources in one column while its edit control only changed one of them, and
more fundamentally "why can this person see that project?" had two possible answers and two places
to look.

Teams alone answer it, and a one-off grant is still expressible as a team of one — with the
advantage that it then has a name and appears in a list, instead of being invisible state on a
user row that nobody can account for six months later.

Safe to drop rather than deprecate: `user_projects` shipped hours earlier in the same session and
holds no rows.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_teams_only_access"
down_revision: Union[str, None] = "0023_teams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_user_projects_project", table_name="user_projects")
    op.drop_table("user_projects")


def downgrade() -> None:
    op.create_table(
        "user_projects",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "project",
            sa.Text(),
            sa.ForeignKey("projects.name", onupdate="CASCADE", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_user_projects_project", "user_projects", ["project"])
