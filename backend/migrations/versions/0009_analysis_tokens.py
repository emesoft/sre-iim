"""add input_tokens/output_tokens to analyses

Revision ID: 0009_analysis_tokens
Revises: 0008_poll_alarm_count
Create Date: 2026-08-22

Only the claude_cli provider currently reports token usage (from `claude -p`'s JSON `usage`
field) — Bedrock/DeepSeek analyses leave these NULL, meaning "not tracked", never 0. A cache-HIT
analysis is explicitly 0/0 (no LLM call was made), set in IngestIncident._copy_analysis.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_analysis_tokens"
down_revision: Union[str, None] = "0008_poll_alarm_count"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("analyses", sa.Column("output_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("analyses", "output_tokens")
    op.drop_column("analyses", "input_tokens")
