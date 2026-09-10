"""add password_hash/role to users, drop provider

Revision ID: 0014_users_auth
Revises: 0013_projects
Create Date: 2026-09-08

`users` (from 0001_initial.py) was an orphaned, unused OAuth-shaped table (email/name/provider, no
password/role). This turns it into the backing store for real per-user login + RBAC: adds
`password_hash` (bcrypt) and `role` (admin | sre | consultant, default 'consultant' so any
pre-existing row — there shouldn't be any, since the table was never used — doesn't end up with a
NULL role), and drops the unused `provider` column. The existing `email` unique constraint from
0001_initial.py is kept.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_users_auth"
down_revision: Union[str, None] = "0013_projects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "users",
        sa.Column("role", sa.Text(), nullable=False, server_default="consultant"),
    )
    op.alter_column("users", "password_hash", server_default=None)
    op.alter_column("users", "role", server_default=None)
    op.drop_column("users", "provider")


def downgrade() -> None:
    op.add_column("users", sa.Column("provider", sa.Text(), nullable=False, server_default="google"))
    op.alter_column("users", "provider", server_default=None)
    op.drop_column("users", "role")
    op.drop_column("users", "password_hash")
