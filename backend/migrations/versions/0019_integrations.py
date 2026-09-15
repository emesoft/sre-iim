"""replace cloud_connections/ado_connections with a provider-agnostic integrations table

Revision ID: 0019_integrations
Revises: 0018_cloud_connection_enabled
Create Date: 2026-09-12

`cloud_connections` was shaped for AWS and then had a second provider pushed through the same
columns: for `cloud="newrelic"` the `region` column held a New Relic **account ID**, and
`sso_profile_name`/`encrypted_access_key_id` sat unused. Adding Azure, Sentry or Datadog would
have meant more of the same. `ado_connections` was a third, near-identical table.

The replacement keeps the parts that are genuinely common to every provider as columns
(project/env/provider/enabled) and moves everything provider-specific into `config` (non-secret)
and `encrypted_secrets` (each value individually encrypted, as before — this migration moves
ciphertext, it never decrypts). `capabilities` is what lets one AWS credential serve alarms, logs
and cost at once while a New Relic one only does alarms.

Health moves to its own row per capability: a failing cost sync says nothing about whether alarm
polling is working, which the single `last_poll_status` column could not express.

Ids are preserved, so `tracked_alarms.connection_id` keeps pointing at the same logical
connection — the alarm state machine would otherwise treat every alarm as new and re-open
incidents for all of them. The legacy tables are deliberately NOT dropped here: nothing writes to
them after this, and they are the fallback if the backfill got something wrong. A later migration
drops them once this has run in anger.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_integrations"
down_revision: Union[str, None] = "0018_cloud_connection_enabled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project",
            sa.Text(),
            sa.ForeignKey("projects.name", onupdate="CASCADE", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("env", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "encrypted_secrets",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "capabilities",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_integrations_project", "integrations", ["project"])

    op.create_table(
        "integration_health",
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integrations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("capability", sa.Text(), primary_key=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=True),
    )

    # --- backfill -----------------------------------------------------------------------------
    # AWS: region/auth stay as config; both key halves keep their existing ciphertext.
    op.execute(
        """
        INSERT INTO integrations
            (id, project, env, provider, enabled, config, encrypted_secrets, capabilities, created_at)
        SELECT
            id, project, env, 'aws', enabled,
            jsonb_strip_nulls(jsonb_build_object(
                'region', region,
                'auth_type', auth_type,
                'sso_profile_name', sso_profile_name
            )),
            jsonb_strip_nulls(jsonb_build_object(
                'access_key_id', encrypted_access_key_id,
                'secret_access_key', encrypted_secret_access_key
            )),
            ARRAY['alarms', 'logs']::text[],
            created_at
        FROM cloud_connections
        WHERE cloud = 'aws'
        """
    )
    # New Relic: `region` was never a region — it becomes the account ID it always was.
    op.execute(
        """
        INSERT INTO integrations
            (id, project, env, provider, enabled, config, encrypted_secrets, capabilities, created_at)
        SELECT
            id, project, env, 'newrelic', enabled,
            jsonb_strip_nulls(jsonb_build_object('account_id', region)),
            jsonb_strip_nulls(jsonb_build_object('api_key', encrypted_secret_access_key)),
            ARRAY['alarms']::text[],
            created_at
        FROM cloud_connections
        WHERE cloud = 'newrelic'
        """
    )
    # Azure DevOps was a separate table for what is the same thing: a per-project integration,
    # with the "tickets" capability instead of "alarms". It has no env axis, hence 'all'.
    op.execute(
        """
        INSERT INTO integrations
            (id, project, env, provider, enabled, config, encrypted_secrets, capabilities, created_at)
        SELECT
            id, project, 'all', 'azure_devops', true,
            jsonb_build_object(
                'organization', org,
                'ado_project', ado_project,
                'work_item_type', work_item_type
            ),
            jsonb_build_object('pat', encrypted_pat),
            ARRAY['tickets']::text[],
            created_at
        FROM ado_connections
        """
    )
    op.execute(
        """
        INSERT INTO integration_health
            (integration_id, capability, last_run_at, status, error, item_count)
        SELECT id, 'alarms', last_poll_at, last_poll_status, last_poll_error, last_poll_alarm_count
        FROM cloud_connections
        WHERE last_poll_status IS NOT NULL
        """
    )

    # Ids were preserved above, so this re-points the existing rows without touching them.
    op.drop_constraint("tracked_alarms_connection_id_fkey", "tracked_alarms", type_="foreignkey")
    op.create_foreign_key(
        "tracked_alarms_integration_id_fkey",
        "tracked_alarms",
        "integrations",
        ["connection_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("tracked_alarms_integration_id_fkey", "tracked_alarms", type_="foreignkey")
    op.create_foreign_key(
        "tracked_alarms_connection_id_fkey",
        "tracked_alarms",
        "cloud_connections",
        ["connection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_table("integration_health")
    op.drop_index("ix_integrations_project", table_name="integrations")
    op.drop_table("integrations")
