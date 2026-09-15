"""record cached input tokens separately from fresh ones

Revision ID: 0027_cached_input_tokens
Revises: 0026_llm_profile_attribution
Create Date: 2026-09-13

`input_tokens` has been the sum of three things that cost very different amounts: fresh input,
cache reads (a fraction of the price) and cache writes (slightly more). One number meant a chat
turn reported "92,897 in" when almost all of it was the same cached prefix — system prompt,
incident context, transcript — re-read on every turn. Read as spend that is wrong by roughly an
order of magnitude, and summing the column across turns counts the same prefix again and again.

So `input_tokens` now means *fresh* input only, and `cached_input_tokens` holds the rest. Existing
rows keep their combined figure in `input_tokens` with NULL cached, which is the honest
representation of "we didn't record the split at the time" — backfilling a guess would invent
precision that was never measured.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_cached_input_tokens"
down_revision: Union[str, None] = "0026_llm_profile_attribution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("analyses", "chat_messages"):
        op.add_column(table, sa.Column("cached_input_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in ("analyses", "chat_messages"):
        op.drop_column(table, "cached_input_tokens")
