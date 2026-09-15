"""record which model profile ran each analysis, and let a group have its own

Revision ID: 0026_llm_profile_attribution
Revises: 0025_groups
Create Date: 2026-09-13

Two columns, one purpose: answering "who spent this".

`analyses.llm_profile` is the name of the model profile that produced the analysis. The model id
alone can't answer it — two teams billing to two different API keys can both be running
claude-opus-5, and the usage table would show them as one line.

`groups.model_profile_id` lets a cohort bring its own key. Precedence at resolution time is
project override > group profile > default, and the order is deliberate: a per-project rule says
where a customer's data is *allowed* to go, a per-group rule says who pays. A constraint outranks
a preference.

Both are nullable and both mean "nothing special": existing analyses keep a NULL profile (they
predate attribution and shouldn't be attributed by guesswork), and a group without a profile
follows the default like everyone else.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_llm_profile_attribution"
down_revision: Union[str, None] = "0025_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("llm_profile", sa.Text(), nullable=True))
    op.create_index("ix_analyses_llm_profile", "analyses", ["llm_profile"])
    # Deliberately not a foreign key: profiles live in an encrypted JSON blob in app_settings, not
    # in a table, and the name is stored rather than the id so a deleted profile's spend stays
    # attributable instead of becoming an orphaned uuid nobody can read.
    op.add_column("groups", sa.Column("model_profile_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("groups", "model_profile_id")
    op.drop_index("ix_analyses_llm_profile", table_name="analyses")
    op.drop_column("analyses", "llm_profile")
