"""core and registry schemas with row-level security

Revision ID: 0001
Revises:
Created: 2026-08-13

Phase 1 foundation. Creates the identity and tenancy tables the control plane
uses today, the registry tables later phases will populate, and the row-level
security policies that make multi-tenancy a database guarantee rather than a code
convention (ADR 0005).

Three things here are deliberate and worth reading before changing:

  1. The service connects as ``janus_app``, a role without ``BYPASSRLS``. The
     owner role is used only for migrations.
  2. Tenant tables are ``FORCE ROW LEVEL SECURITY``, so policies apply even to
     the owner — a superuser connection is the only way past them, and that is
     never how the service connects.
  3. ``core.authenticate_api_key`` is ``SECURITY DEFINER`` because API key
     authentication has to happen *before* tenant context exists. It returns one
     row's authentication fields and nothing else, which keeps the single
     necessary cross-tenant read small and auditable.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "janus_app"

#: Local development convenience only. Deployed environments create the role
#: out of band with a password from Secrets Manager, and this migration then
#: only grants privileges.
APP_ROLE_PASSWORD = os.environ.get("JANUS_APP_DB_PASSWORD", "janus_app")

TENANT_TABLES = ("core.api_keys", "core.audit_events", "core.organization_members")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS core")
    op.execute("CREATE SCHEMA IF NOT EXISTS registry")

    _create_enums()
    _create_core_tables()
    _create_registry_tables()
    _create_app_role()
    _enable_row_level_security()
    _create_api_key_authentication_function()

    op.execute(
        """
        INSERT INTO core.schema_metadata (key, value, phase)
        VALUES ('schema_version', '0001', 1)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value, phase = excluded.phase
        """
    )


def _create_enums() -> None:
    op.execute(
        "CREATE TYPE execution_mode AS ENUM ('auto','cloud','private','sovereign','offline')"
    )
    op.execute(
        "CREATE TYPE classification AS ENUM ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')"
    )
    op.execute("CREATE TYPE org_role AS ENUM ('owner','admin','member','viewer','billing')")
    op.execute("CREATE TYPE policy_scope AS ENUM ('platform','organization','team','agent')")
    op.execute(
        "CREATE TYPE model_type AS ENUM "
        "('chat','embedding','rerank','transcription','speech','image')"
    )
    op.execute(
        "CREATE TYPE model_status AS ENUM ('draft','evaluating','active','deprecated','disabled')"
    )
    op.execute(
        "CREATE TYPE deployment_type AS ENUM "
        "('provider_cloud','janus_gpu','janus_cpu','local_dev','customer_vpc')"
    )
    op.execute("CREATE TYPE privacy_level AS ENUM ('provider','private','local')")
    op.execute(
        "CREATE TYPE health_state AS ENUM "
        "('ready','warming','overloaded','degraded','offline','draining','provisioning')"
    )


def _create_core_tables() -> None:
    op.execute(
        """
        CREATE TABLE core.organizations (
          id                     TEXT PRIMARY KEY,
          slug                   TEXT NOT NULL UNIQUE,
          name                   TEXT NOT NULL,
          plan                   TEXT NOT NULL DEFAULT 'free',
          default_mode           execution_mode NOT NULL DEFAULT 'auto',
          default_classification classification NOT NULL DEFAULT 'INTERNAL',
          data_residency         TEXT[] NOT NULL DEFAULT '{}',
          settings               JSONB NOT NULL DEFAULT '{}',
          created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at             TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE core.users (
          id             TEXT PRIMARY KEY,
          email          TEXT NOT NULL UNIQUE,
          email_verified BOOLEAN NOT NULL DEFAULT false,
          name           TEXT,
          avatar_url     TEXT,
          password_hash  TEXT,
          mfa_secret_ref TEXT,
          locale         TEXT NOT NULL DEFAULT 'en',
          status         TEXT NOT NULL DEFAULT 'active',
          last_login_at  TIMESTAMPTZ,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at     TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE core.organization_members (
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          user_id         TEXT NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
          role            org_role NOT NULL DEFAULT 'member',
          invited_by      TEXT REFERENCES core.users(id),
          joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, user_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_organization_members_user ON core.organization_members (user_id)")
    op.execute(
        """
        CREATE TABLE core.teams (
          id              TEXT PRIMARY KEY,
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          name            TEXT NOT NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (organization_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE core.team_members (
          team_id TEXT NOT NULL REFERENCES core.teams(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
          role    TEXT NOT NULL DEFAULT 'member',
          PRIMARY KEY (team_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE core.api_keys (
          id              TEXT PRIMARY KEY,
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          created_by      TEXT NOT NULL REFERENCES core.users(id),
          name            TEXT NOT NULL,
          prefix          TEXT NOT NULL,
          key_hash        TEXT NOT NULL,
          lookup_hash     TEXT NOT NULL,
          scopes          TEXT[] NOT NULL DEFAULT '{}',
          mode_ceiling    execution_mode,
          rate_limit      JSONB NOT NULL DEFAULT '{}',
          last_used_at    TIMESTAMPTZ,
          expires_at      TIMESTAMPTZ,
          revoked_at      TIMESTAMPTZ,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_api_keys_organization_active ON core.api_keys (organization_id) "
        "WHERE revoked_at IS NULL"
    )
    op.execute("CREATE UNIQUE INDEX ux_api_keys_key_hash ON core.api_keys (key_hash)")
    op.execute("CREATE UNIQUE INDEX ux_api_keys_lookup_hash ON core.api_keys (lookup_hash)")
    op.execute(
        """
        CREATE TABLE core.sessions (
          id              TEXT PRIMARY KEY,
          user_id         TEXT NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
          organization_id TEXT REFERENCES core.organizations(id) ON DELETE SET NULL,
          token_hash      TEXT NOT NULL UNIQUE,
          ip              TEXT,
          user_agent      TEXT,
          expires_at      TIMESTAMPTZ NOT NULL,
          revoked_at      TIMESTAMPTZ,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_sessions_user ON core.sessions (user_id)")
    op.execute(
        """
        CREATE TABLE core.audit_events (
          id              TEXT PRIMARY KEY,
          organization_id TEXT REFERENCES core.organizations(id) ON DELETE SET NULL,
          actor_type      TEXT NOT NULL,
          actor_id        TEXT,
          action          TEXT NOT NULL,
          resource_type   TEXT NOT NULL,
          resource_id     TEXT,
          ip              TEXT,
          metadata        JSONB NOT NULL DEFAULT '{}',
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_audit_events_org_created "
        "ON core.audit_events (organization_id, created_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE core.policies (
          id                   TEXT PRIMARY KEY,
          scope                policy_scope NOT NULL,
          scope_id             TEXT,
          organization_id      TEXT REFERENCES core.organizations(id) ON DELETE CASCADE,
          version              INTEGER NOT NULL DEFAULT 1,
          mode                 execution_mode,
          weight_profile       TEXT,
          weights              JSONB NOT NULL DEFAULT '{}',
          allow                JSONB NOT NULL DEFAULT '{}',
          deny                 JSONB NOT NULL DEFAULT '{}',
          limits               JSONB NOT NULL DEFAULT '{}',
          classification_rules JSONB NOT NULL DEFAULT '{}',
          fallback             JSONB NOT NULL
                                 DEFAULT jsonb_build_object('enabled', true, 'max_attempts', 3),
          is_active            BOOLEAN NOT NULL DEFAULT true,
          created_by           TEXT REFERENCES core.users(id),
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (scope, scope_id, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE core.schema_metadata (
          key   TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          phase INTEGER NOT NULL DEFAULT 1
        )
        """
    )


def _create_registry_tables() -> None:
    """Platform-scoped catalog (ADR 0007): one fleet, many policies.

    Phase 1 serves the catalog from reviewed YAML in ``registry/``; these tables
    are the destination for the sync job that lands with the first cloud provider
    in Phase 2. Creating them now keeps the schema and the documentation aligned.
    """
    op.execute(
        """
        CREATE TABLE registry.providers (
          id           TEXT PRIMARY KEY,
          slug         TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL,
          kind         TEXT NOT NULL,
          status       TEXT NOT NULL DEFAULT 'active',
          metadata     JSONB NOT NULL DEFAULT '{}',
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE registry.licenses (
          id                          TEXT PRIMARY KEY,
          name                        TEXT NOT NULL,
          spdx_id                     TEXT,
          url                         TEXT,
          commercial_use              TEXT NOT NULL,
          attribution_required        BOOLEAN NOT NULL DEFAULT false,
          attribution_text            TEXT,
          redistribution_of_weights   TEXT,
          acceptable_use_restrictions TEXT[] NOT NULL DEFAULT '{}',
          reviewed_by                 TEXT REFERENCES core.users(id),
          reviewed_at                 TIMESTAMPTZ,
          review_notes                TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE registry.models (
          id                TEXT PRIMARY KEY,
          slug              TEXT NOT NULL UNIQUE,
          display_name      TEXT NOT NULL,
          family            TEXT,
          version           TEXT,
          provider_id       TEXT NOT NULL REFERENCES registry.providers(id),
          type              model_type NOT NULL DEFAULT 'chat',
          parameters        TEXT,
          architecture      TEXT,
          context_window    INTEGER NOT NULL,
          max_output_tokens INTEGER,
          input_modalities  TEXT[] NOT NULL DEFAULT '{text}',
          output_modalities TEXT[] NOT NULL DEFAULT '{text}',
          languages         TEXT[] NOT NULL DEFAULT '{}',
          capabilities      JSONB NOT NULL DEFAULT '{}',
          cost_class        TEXT,
          latency_class     TEXT,
          tier              TEXT,
          status            model_status NOT NULL DEFAULT 'draft',
          license_id        TEXT REFERENCES registry.licenses(id),
          weights_source    TEXT,
          weights_sha256    TEXT,
          quantization      TEXT,
          metadata_verified BOOLEAN NOT NULL DEFAULT false,
          notes             TEXT,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_models_status_type ON registry.models (status, type)")
    op.execute("CREATE INDEX ix_models_capabilities ON registry.models USING GIN (capabilities)")
    op.execute("CREATE INDEX ix_models_languages ON registry.models USING GIN (languages)")
    op.execute(
        """
        CREATE TABLE registry.model_deployments (
          id                   TEXT PRIMARY KEY,
          key                  TEXT NOT NULL,
          model_id             TEXT NOT NULL REFERENCES registry.models(id) ON DELETE CASCADE,
          backend              TEXT NOT NULL,
          protocol             TEXT NOT NULL DEFAULT 'openai_compatible',
          endpoint_ref         TEXT,
          region               TEXT,
          deployment_type      deployment_type NOT NULL,
          privacy_level        privacy_level NOT NULL,
          data_residency       TEXT[] NOT NULL DEFAULT '{}',
          hardware             JSONB NOT NULL DEFAULT '{}',
          replicas             JSONB NOT NULL DEFAULT '{}',
          max_context          INTEGER,
          max_concurrency      INTEGER,
          scale_to_zero        BOOLEAN NOT NULL DEFAULT false,
          warm_pool_floor      INTEGER NOT NULL DEFAULT 0,
          capability_overrides JSONB NOT NULL DEFAULT '{}',
          cost_basis           JSONB NOT NULL DEFAULT '{}',
          credentials_ref      TEXT,
          status               health_state NOT NULL DEFAULT 'provisioning',
          is_enabled           BOOLEAN NOT NULL DEFAULT true,
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (model_id, key)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_model_deployments_routable "
        "ON registry.model_deployments (status, is_enabled, privacy_level)"
    )
    op.execute(
        """
        CREATE TABLE registry.model_aliases (
          alias       TEXT PRIMARY KEY,
          description TEXT,
          selection   JSONB NOT NULL DEFAULT '{}',
          members     TEXT[] NOT NULL DEFAULT '{}',
          membership  TEXT NOT NULL DEFAULT 'dynamic'
        )
        """
    )
    op.execute(
        """
        CREATE TABLE registry.model_prices (
          id                      TEXT PRIMARY KEY,
          model_id                TEXT NOT NULL REFERENCES registry.models(id) ON DELETE CASCADE,
          deployment_id           TEXT REFERENCES registry.model_deployments(id) ON DELETE CASCADE,
          input_per_1m_usd        NUMERIC(18,8),
          output_per_1m_usd       NUMERIC(18,8),
          cached_input_per_1m_usd NUMERIC(18,8),
          gpu_hour_usd            NUMERIC(18,8),
          currency                TEXT NOT NULL DEFAULT 'USD',
          effective_from          TIMESTAMPTZ NOT NULL,
          effective_to            TIMESTAMPTZ,
          source                  TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_model_prices_effective "
        "ON registry.model_prices (model_id, effective_from DESC)"
    )


def _quote_literal(value: str) -> str:
    """Quote a string for SQL text by doubling embedded single quotes.

    PostgreSQL does not accept bind parameters in DDL, and ``CREATE ROLE`` is
    DDL, so the password has to be part of the statement text. Quoting it here
    means a password containing an apostrophe is stored correctly instead of
    producing a syntax error — or worse, running as SQL.
    """
    return "'" + value.replace("'", "''") + "'"


def _create_app_role() -> None:
    exists = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE})
        .scalar()
    )
    if not exists:
        op.execute(
            f"CREATE ROLE {APP_ROLE} LOGIN "
            f"PASSWORD {_quote_literal(APP_ROLE_PASSWORD)} "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
        )

    op.execute(f"GRANT USAGE ON SCHEMA core, registry TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO {APP_ROLE}")
    # The catalog is operated through reviewed configuration, so the running
    # service reads it and never writes it.
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA registry TO {APP_ROLE}")
    # Audit events are append-only: no UPDATE, no DELETE, for any application role.
    op.execute(f"REVOKE UPDATE, DELETE ON core.audit_events FROM {APP_ROLE}")


def _enable_row_level_security() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE makes the policies apply to the table owner too, so a test or a
        # migration cannot accidentally "prove" isolation while bypassing it.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    tenant_predicate = "organization_id = current_setting('janus.organization_id', true)"

    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON core.api_keys
          USING ({tenant_predicate})
          WITH CHECK ({tenant_predicate})
        """
    )
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON core.audit_events
          USING ({tenant_predicate})
          WITH CHECK ({tenant_predicate})
        """
    )
    # Membership is legitimately cross-tenant in one direction: a user must be
    # able to list the organizations they belong to before choosing one. The
    # policy accepts either scope so that query needs no exception.
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON core.organization_members
          USING (
            {tenant_predicate}
            OR user_id = current_setting('janus.user_id', true)
          )
          WITH CHECK ({tenant_predicate})
        """
    )


def _create_api_key_authentication_function() -> None:
    # One statement per execute: asyncpg prepares statements, and a prepared
    # statement cannot carry several commands.
    op.execute(
        """
        CREATE FUNCTION core.authenticate_api_key(lookup_hash_input TEXT)
        RETURNS TABLE (
          id              TEXT,
          organization_id TEXT,
          key_hash        TEXT,
          scopes          TEXT[],
          mode_ceiling    execution_mode,
          revoked_at      TIMESTAMPTZ,
          expires_at      TIMESTAMPTZ
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = core, pg_temp
        AS $$
          SELECT k.id, k.organization_id, k.key_hash, k.scopes,
                 k.mode_ceiling, k.revoked_at, k.expires_at
          FROM core.api_keys k
          WHERE k.lookup_hash = lookup_hash_input
          LIMIT 1
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION core.authenticate_api_key(TEXT) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION core.authenticate_api_key(TEXT) TO {APP_ROLE}")


def downgrade() -> None:
    """For local development. Production rollback is a forward fix."""
    op.execute("DROP FUNCTION IF EXISTS core.authenticate_api_key(TEXT)")
    op.execute("DROP SCHEMA IF EXISTS registry CASCADE")
    op.execute("DROP SCHEMA IF EXISTS core CASCADE")
    for enum in (
        "health_state",
        "privacy_level",
        "deployment_type",
        "model_status",
        "model_type",
        "policy_scope",
        "org_role",
        "classification",
        "execution_mode",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum}")
