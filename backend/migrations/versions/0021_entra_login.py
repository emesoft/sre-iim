"""allow Microsoft Entra ID accounts alongside local password accounts

Revision ID: 0021_entra_login
Revises: 0020_project_auto_analyze
Create Date: 2026-09-12

An Entra-authenticated user has no password in this system — the identity provider holds it. So
`password_hash` becomes nullable and `auth_provider` records where the account came from, which
keeps the password login path honest: a NULL hash can never verify, rather than an account being
reachable through a blank or placeholder password.

`external_id` stores Entra's `oid` (object id), which is the only stable identifier: usernames and
email addresses change, `oid` doesn't. It's unique per provider so two accounts can't claim the
same Entra identity.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_entra_login"
down_revision: Union[str, None] = "0020_project_auto_analyze"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("auth_provider", sa.Text(), nullable=False, server_default="local"),
    )
    op.add_column("users", sa.Column("external_id", sa.Text(), nullable=True))
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)
    op.create_unique_constraint(
        "uq_users_provider_external_id", "users", ["auth_provider", "external_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_provider_external_id", "users", type_="unique")
    # Accounts with no password can't exist under the old schema; they came from Entra and have
    # no local credential to fall back on, so removing them is the only correct direction.
    op.execute("DELETE FROM users WHERE password_hash IS NULL")
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)
    op.drop_column("users", "external_id")
    op.drop_column("users", "auth_provider")
