"""login by username instead of email

Revision ID: 0015_users_username
Revises: 0014_users_auth
Create Date: 2026-09-10

Login moves from email+password to username+password. `username` becomes the unique login
identity; `email` becomes optional, purely informational (no longer required, no longer unique).

Adds `username` as nullable first, backfills it from the existing `email` column (local-part
before `@`, or `'user'` if email is null — good enough for a dev-only backfill; there shouldn't be
more than the one seeded admin row in practice), then tightens it to NOT NULL + UNIQUE. Drops the
old unique constraint on `email` (created inline by `unique=True` in 0001_initial.py, which
Postgres names `users_email_key` by default — no explicit name was given there) and relaxes
`email` to nullable.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_users_username"
down_revision: Union[str, None] = "0014_users_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.Text(), nullable=True))

    # Backfill: local-part of email before '@', or 'user' if email is null/blank.
    op.execute(
        """
        UPDATE users
        SET username = COALESCE(NULLIF(split_part(email, '@', 1), ''), 'user')
        WHERE username IS NULL
        """
    )
    # De-dupe any backfilled collisions (e.g. two rows both falling back to 'user') by suffixing
    # the row id — cheap and sufficient for a dev-only backfill, not a real uniqueness algorithm.
    op.execute(
        """
        UPDATE users u
        SET username = u.username || '-' || substr(u.id::text, 1, 8)
        WHERE u.username IN (
            SELECT username FROM users GROUP BY username HAVING count(*) > 1
        )
        """
    )

    op.alter_column("users", "username", nullable=False)
    op.create_unique_constraint("uq_users_username", "users", ["username"])

    op.drop_constraint("users_email_key", "users", type_="unique")
    op.alter_column("users", "email", nullable=True)


def downgrade() -> None:
    op.alter_column("users", "email", nullable=False)
    op.create_unique_constraint("users_email_key", "users", ["email"])
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "username")
