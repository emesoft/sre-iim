"""grant project access through teams, not only one user at a time

Revision ID: 0023_teams
Revises: 0022_user_projects
Create Date: 2026-09-12

Direct user→project grants (migration 0022) are O(people × projects) to administer: every new
project means revisiting every account. A team assigns its projects once and people join the team,
which is the shape that survives a growing org.

`user_projects` stays as the escape hatch for one-offs — a consultant who needs one project for two
weeks shouldn't require a team to exist. Access is the union of both, so neither can silently
remove what the other granted.

Done now rather than later because `user_projects` is still empty: adopting teams once there are
dozens of direct grants would mean a merge migration plus a per-row judgement call about which team
each one belonged to.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_teams"
down_revision: Union[str, None] = "0022_user_projects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "team_projects",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "project",
            sa.Text(),
            sa.ForeignKey("projects.name", onupdate="CASCADE", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "team_members",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    # Both joins run on every request that resolves a scope, from the user's side.
    op.create_index("ix_team_members_user", "team_members", ["user_id"])
    op.create_index("ix_team_projects_project", "team_projects", ["project"])


def downgrade() -> None:
    op.drop_index("ix_team_projects_project", table_name="team_projects")
    op.drop_index("ix_team_members_user", table_name="team_members")
    op.drop_table("team_members")
    op.drop_table("team_projects")
    op.drop_table("teams")
