"""telemetry schema: routing decisions and usage records

Revision ID: 0003
Revises: 0002
Created: 2026-08-14

Phase 3 makes the gateway a durable product surface: every inference request gets
an explainable routing decision and a usage record suitable for quotas and cost.

Registry foreign keys on ``selected_model_id`` / ``deployment_id`` are nullable
because the catalog is still configuration-as-code (ADR 0007). Slugs and keys
are always stored in the JSON payload for explainability.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "janus_app"

TENANT_TABLES = (
    "telemetry.routing_decisions",
    "telemetry.usage_records",
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS telemetry")

    op.execute(
        """
        CREATE TABLE telemetry.routing_decisions (
          id                  TEXT PRIMARY KEY,
          request_id          TEXT NOT NULL,
          organization_id     TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          user_id             TEXT REFERENCES core.users(id),
          api_key_id          TEXT REFERENCES core.api_keys(id),
          conversation_id     TEXT,
          agent_run_id        TEXT,
          requested_model     TEXT NOT NULL,
          mode                execution_mode NOT NULL,
          classification      classification NOT NULL,
          requirements        JSONB NOT NULL DEFAULT '{}',
          policy_id           TEXT,
          policy_version      INTEGER,
          weight_profile      TEXT NOT NULL DEFAULT 'balanced',
          candidates          JSONB NOT NULL DEFAULT '[]',
          selected_model_slug TEXT,
          selected_deployment_key TEXT,
          selected_provider   TEXT,
          routing_reason      TEXT,
          routing_explanation TEXT,
          fallback_attempts   JSONB NOT NULL DEFAULT '[]',
          fallback_used       BOOLEAN NOT NULL DEFAULT false,
          decision_ms         INTEGER,
          error_code          TEXT,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_routing_decisions_request "
        "ON telemetry.routing_decisions (request_id)"
    )
    op.execute(
        "CREATE INDEX ix_routing_decisions_org_time "
        "ON telemetry.routing_decisions (organization_id, created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE telemetry.usage_records (
          id                  TEXT PRIMARY KEY,
          request_id          TEXT NOT NULL,
          organization_id     TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          user_id             TEXT REFERENCES core.users(id),
          api_key_id          TEXT REFERENCES core.api_keys(id),
          conversation_id     TEXT,
          agent_run_id        TEXT,
          model_slug          TEXT,
          deployment_key      TEXT,
          provider            TEXT,
          operation           TEXT NOT NULL DEFAULT 'chat',
          input_tokens        INTEGER NOT NULL DEFAULT 0,
          output_tokens       INTEGER NOT NULL DEFAULT 0,
          cached_tokens       INTEGER NOT NULL DEFAULT 0,
          ttft_ms             INTEGER,
          total_ms            INTEGER,
          tokens_per_sec      NUMERIC(10,2),
          cost_usd            NUMERIC(18,8) NOT NULL DEFAULT 0,
          cost_basis          TEXT,
          fallback_used       BOOLEAN NOT NULL DEFAULT false,
          usage_estimated     BOOLEAN NOT NULL DEFAULT false,
          error_code          TEXT,
          occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_usage_records_org_time "
        "ON telemetry.usage_records (organization_id, occurred_at DESC)"
    )
    op.execute("CREATE INDEX ix_usage_records_request ON telemetry.usage_records (request_id)")

    _grant_app_role()
    _enable_row_level_security()

    op.execute(
        """
        INSERT INTO core.schema_metadata (key, value, phase)
        VALUES ('schema_version', '0003', 3)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value, phase = excluded.phase
        """
    )


def _grant_app_role() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA telemetry TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA telemetry TO {APP_ROLE}")
    # Append-only accounting: the service records usage, it does not rewrite it.
    op.execute(f"REVOKE UPDATE, DELETE ON telemetry.routing_decisions FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE ON telemetry.usage_records FROM {APP_ROLE}")


def _enable_row_level_security() -> None:
    tenant_predicate = "organization_id = current_setting('janus.organization_id', true)"
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING ({tenant_predicate})
              WITH CHECK ({tenant_predicate})
            """
        )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS telemetry CASCADE")
    op.execute(
        """
        UPDATE core.schema_metadata
        SET value = '0002', phase = 2
        WHERE key = 'schema_version'
        """
    )
