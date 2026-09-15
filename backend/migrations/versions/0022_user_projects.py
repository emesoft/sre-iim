"""scope non-admin users to the projects they are a member of

Revision ID: 0022_user_projects
Revises: 0021_entra_login
Create Date: 2026-09-12

Until now `users.role` said what someone could do but nothing about *whose data* — every signed-in
user saw every project's incidents. This adds the second axis: role stays global, membership
decides scope.

Deliberately a plain user↔project join rather than a group entity in between. With a handful of
projects a group layer is indirection that costs more than it saves, and nothing outside this
table would change if one is added later: enforcement only ever asks "which projects may this user
see", and that question can be answered through groups without moving the check.

Existing accounts are **not** backfilled with memberships. Making everyone a member of everything
would silently preserve today's see-everything behaviour and quietly defeat the feature on the one
database that matters; an admin grants access deliberately instead. Admins are unaffected — they
are global by role, not by membership.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_user_projects"
down_revision: Union[str, None] = "0021_entra_login"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_user_projects_project", table_name="user_projects")
    op.drop_table("user_projects")
