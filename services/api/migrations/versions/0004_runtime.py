"""agent, knowledge, citations - Phases 4-6

Revision ID: 0004
Revises: 0003
Created: 2026-08-14

Agents, knowledge bases with pgvector, and message citations. Registry foreign
keys stay off these tables on purpose: the catalog is still YAML-as-code, so a
knowledge base pins an embedding *slug*, not a ``registry.models`` row.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "janus_app"

TENANT_TABLES = (
    "agent.agents",
    "agent.agent_versions",
    "agent.mcp_servers",
    "agent.tools",
    "agent.agent_runs",
    "agent.agent_steps",
    "agent.checkpoints",
    "knowledge.knowledge_bases",
    "knowledge.documents",
    "knowledge.chunks",
    "chat.citations",
    "core.policies",
)

EMBEDDING_DIMENSIONS = 8


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS agent")
    op.execute("CREATE SCHEMA IF NOT EXISTS knowledge")

    op.execute("CREATE TYPE agent_status AS ENUM ('draft','published','archived')")
    op.execute(
        """
        CREATE TYPE run_status AS ENUM (
          'queued','running','awaiting_input','awaiting_approval',
          'completed','failed','cancelled','halted'
        )
        """
    )

    _create_agent_tables()
    _create_knowledge_tables()
    _create_citations()
    _grant_app_role()
    _enable_row_level_security()

    op.execute(
        """
        INSERT INTO core.schema_metadata (key, value, phase)
        VALUES ('schema_version', '0004', 6)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value, phase = excluded.phase
        """
    )


def _create_agent_tables() -> None:
    op.execute(
        """
        CREATE TABLE agent.agents (
          id              TEXT PRIMARY KEY,
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          slug            TEXT NOT NULL,
          name            TEXT NOT NULL,
          description     TEXT,
          status          agent_status NOT NULL DEFAULT 'draft',
          current_version INTEGER NOT NULL DEFAULT 0,
          visibility      TEXT NOT NULL DEFAULT 'organization',
          created_by      TEXT NOT NULL REFERENCES core.users(id),
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at      TIMESTAMPTZ,
          UNIQUE (organization_id, slug)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE agent.agent_versions (
          id                 TEXT PRIMARY KEY,
          agent_id           TEXT NOT NULL REFERENCES agent.agents(id) ON DELETE CASCADE,
          organization_id    TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          version            INTEGER NOT NULL,
          instructions       TEXT NOT NULL,
          capabilities       JSONB NOT NULL DEFAULT '{}',
          model_policy       JSONB NOT NULL DEFAULT '{}',
          memory_config      JSONB NOT NULL DEFAULT '{}',
          knowledge_base_ids TEXT[] NOT NULL DEFAULT '{}',
          tools              TEXT[] NOT NULL DEFAULT '{}',
          published_at       TIMESTAMPTZ,
          published_by       TEXT REFERENCES core.users(id),
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (agent_id, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE agent.mcp_servers (
          id              TEXT PRIMARY KEY,
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          name            TEXT NOT NULL,
          transport       TEXT NOT NULL,
          url             TEXT,
          auth_type       TEXT,
          credentials_ref TEXT,
          scopes          TEXT[] NOT NULL DEFAULT '{}',
          status          TEXT NOT NULL DEFAULT 'active',
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE agent.tools (
          id              TEXT PRIMARY KEY,
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          kind            TEXT NOT NULL,
          name            TEXT NOT NULL,
          description     TEXT NOT NULL,
          input_schema    JSONB NOT NULL,
          mcp_server_id   TEXT REFERENCES agent.mcp_servers(id) ON DELETE CASCADE,
          config          JSONB NOT NULL DEFAULT '{}',
          side_effects    TEXT NOT NULL DEFAULT 'read_only',
          approval        TEXT NOT NULL DEFAULT 'auto',
          timeout_ms      INTEGER NOT NULL DEFAULT 15000,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (organization_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE agent.agent_runs (
          id               TEXT PRIMARY KEY,
          organization_id  TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          agent_id         TEXT NOT NULL REFERENCES agent.agents(id),
          agent_version_id TEXT NOT NULL REFERENCES agent.agent_versions(id),
          conversation_id  TEXT REFERENCES chat.conversations(id) ON DELETE SET NULL,
          triggered_by     TEXT REFERENCES core.users(id),
          status           run_status NOT NULL DEFAULT 'queued',
          mode             execution_mode NOT NULL,
          input            TEXT NOT NULL,
          output           TEXT,
          step_count       INTEGER NOT NULL DEFAULT 0,
          input_tokens     INTEGER NOT NULL DEFAULT 0,
          output_tokens    INTEGER NOT NULL DEFAULT 0,
          cost_usd         NUMERIC(18,8) NOT NULL DEFAULT 0,
          halt_reason      TEXT,
          error            JSONB,
          citations        JSONB NOT NULL DEFAULT '[]',
          started_at       TIMESTAMPTZ,
          finished_at      TIMESTAMPTZ,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON agent.agent_runs (organization_id, created_at DESC)")
    op.execute(
        """
        CREATE TABLE agent.agent_steps (
          id              TEXT PRIMARY KEY,
          run_id          TEXT NOT NULL REFERENCES agent.agent_runs(id) ON DELETE CASCADE,
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          sequence        INTEGER NOT NULL,
          node            TEXT NOT NULL,
          model_slug      TEXT,
          request_id      TEXT,
          tool_name       TEXT,
          tool_input      JSONB,
          tool_output     JSONB,
          input_tokens    INTEGER,
          output_tokens   INTEGER,
          status          TEXT NOT NULL,
          error           JSONB,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (run_id, sequence)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE agent.checkpoints (
          run_id          TEXT NOT NULL REFERENCES agent.agent_runs(id) ON DELETE CASCADE,
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          step            INTEGER NOT NULL,
          state           JSONB NOT NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (run_id, step)
        )
        """
    )


def _create_knowledge_tables() -> None:
    op.execute(
        f"""
        CREATE TABLE knowledge.knowledge_bases (
          id                   TEXT PRIMARY KEY,
          organization_id      TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          name                 TEXT NOT NULL,
          description          TEXT,
          classification       classification NOT NULL DEFAULT 'INTERNAL',
          embedding_model      TEXT NOT NULL,
          embedding_dimensions INTEGER NOT NULL DEFAULT {EMBEDDING_DIMENSIONS},
          chunk_config         JSONB NOT NULL DEFAULT '{{}}',
          document_count       INTEGER NOT NULL DEFAULT 0,
          created_by           TEXT NOT NULL REFERENCES core.users(id),
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (organization_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge.documents (
          id                TEXT PRIMARY KEY,
          knowledge_base_id TEXT NOT NULL
            REFERENCES knowledge.knowledge_bases(id) ON DELETE CASCADE,
          organization_id   TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          title             TEXT,
          source_type       TEXT NOT NULL,
          mime_type         TEXT,
          size_bytes        BIGINT,
          content_sha256    TEXT,
          language          TEXT,
          classification    classification NOT NULL DEFAULT 'INTERNAL',
          status            TEXT NOT NULL DEFAULT 'pending',
          error             JSONB,
          chunk_count       INTEGER NOT NULL DEFAULT 0,
          created_by        TEXT REFERENCES core.users(id),
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON knowledge.documents (knowledge_base_id, status)")
    op.execute(
        """
        CREATE UNIQUE INDEX ON knowledge.documents (knowledge_base_id, content_sha256)
          WHERE content_sha256 IS NOT NULL
        """
    )
    op.execute(
        f"""
        CREATE TABLE knowledge.chunks (
          id                   TEXT PRIMARY KEY,
          document_id          TEXT NOT NULL REFERENCES knowledge.documents(id) ON DELETE CASCADE,
          knowledge_base_id    TEXT NOT NULL
            REFERENCES knowledge.knowledge_bases(id) ON DELETE CASCADE,
          organization_id      TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          sequence             INTEGER NOT NULL,
          content              TEXT NOT NULL,
          token_count          INTEGER,
          embedding            vector({EMBEDDING_DIMENSIONS}),
          embedding_model      TEXT NOT NULL,
          embedding_version    TEXT NOT NULL DEFAULT '1',
          metadata             JSONB NOT NULL DEFAULT '{{}}',
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (document_id, sequence)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX chunks_embedding_hnsw ON knowledge.chunks
          USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
        """
    )
    op.execute("CREATE INDEX ON knowledge.chunks (knowledge_base_id)")
    op.execute(
        """
        CREATE INDEX chunks_content_fts ON knowledge.chunks
          USING GIN (to_tsvector('simple', content))
        """
    )


def _create_citations() -> None:
    op.execute(
        """
        CREATE TABLE chat.citations (
          id              TEXT PRIMARY KEY,
          message_id      TEXT REFERENCES chat.messages(id) ON DELETE CASCADE,
          run_id          TEXT,
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          chunk_id        TEXT,
          document_id     TEXT,
          quote           TEXT,
          score           NUMERIC(6,5),
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def _grant_app_role() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA agent, knowledge TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA agent TO {APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA knowledge TO {APP_ROLE}"
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON chat.citations TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON core.policies TO {APP_ROLE}")


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
    op.execute("DROP TABLE IF EXISTS chat.citations CASCADE")
    op.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")
    op.execute("DROP SCHEMA IF EXISTS agent CASCADE")
    op.execute("DROP TYPE IF EXISTS run_status")
    op.execute("DROP TYPE IF EXISTS agent_status")
    op.execute(
        """
        UPDATE core.schema_metadata
        SET value = '0003', phase = 3
        WHERE key = 'schema_version'
        """
    )
