"""replace roles + teams with one concept: groups

Revision ID: 0025_groups
Revises: 0024_teams_only_access
Create Date: 2026-09-13

The admin screen had two columns that both read as "which group are you in": a `role` dropdown
(admin / sre / consultant) and a team picker. They answered different questions — what you may do
vs whose data you may see — but nothing on screen said so, and every attempt to lay them out side
by side read as two systems bolted together.

A group answers both at once: it has a permission level *and* the projects it opens up. "SRE · gcm"
is one row, one dropdown, one place to look. A user belongs to exactly one group; belonging to none
is the Guest state every new account starts in — no permissions, no projects, the "ask an admin"
screen.

`users.role` therefore stops being stored: it is the group's role, read through the join, so the
two can't drift. Users without a group resolve to "guest", which no route accepts.

Backfill preserves today's behavior exactly: one group per role currently in use, granting no
projects — which is what non-admins can see today anyway (the teams tables are empty). The
migration refuses to run if any team membership exists, rather than silently revoking access it
can't map: a team's projects and a member's role are two different axes, and folding them together
is a judgement call a migration shouldn't make on its own.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_groups"
down_revision: Union[str, None] = "0024_teams_only_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    memberships = conn.execute(sa.text("SELECT count(*) FROM team_members")).scalar_one()
    if memberships:
        raise RuntimeError(
            f"{memberships} team membership(s) exist; a team's projects and a member's role map "
            "onto groups only by a human decision. Create the groups by hand, move people over, "
            "empty team_members, then run this."
        )

    op.create_table(
        "groups",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        # admin | sre | consultant — what members of this group may do.
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "group_projects",
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "project",
            sa.Text(),
            sa.ForeignKey("projects.name", onupdate="CASCADE", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_group_projects_project", "group_projects", ["project"])

    op.add_column(
        "users", sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_users_group_id", "users", "groups", ["group_id"], ["id"], ondelete="SET NULL"
    )

    # One group per role in use, named the way a person would name it.
    op.execute(
        """
        INSERT INTO groups (name, role, description)
        SELECT CASE r.role WHEN 'sre' THEN 'SRE' ELSE initcap(r.role) END,
               r.role,
               'Carried over from the ' || r.role || ' role. Add the projects it should open up.'
        FROM (SELECT DISTINCT role FROM users) r
        """
    )
    op.execute("UPDATE users u SET group_id = g.id FROM groups g WHERE g.role = u.role")

    op.drop_column("users", "role")
    op.drop_table("team_members")
    op.drop_table("team_projects")
    op.drop_table("teams")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.Text(), nullable=False, server_default="consultant"),
    )
    op.execute("UPDATE users u SET role = g.role FROM groups g WHERE g.id = u.group_id")
    op.drop_constraint("fk_users_group_id", "users", type_="foreignkey")
    op.drop_column("users", "group_id")
    op.drop_index("ix_group_projects_project", table_name="group_projects")
    op.drop_table("group_projects")
    op.drop_table("groups")

    op.create_table(
        "teams",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
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
    op.create_index("ix_team_projects_project", "team_projects", ["project"])
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
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])
