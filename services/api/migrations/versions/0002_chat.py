"""chat schema: conversations, messages, attachments

Revision ID: 0002
Revises: 0001
Created: 2026-08-13

Phase 2 turns the Phase 1 passthrough into a chat product, which means history has
to survive a page reload. This migration adds the ``chat`` schema and the same
row-level security guarantees the ``core`` tables already carry.

Four decisions here are deliberate and worth reading before changing:

  1. **Attribution is stored as the gateway's identifiers**, not as foreign keys
     into ``registry.models``. The catalog is configuration-as-code until Phase 3
     (ADR 0007), so those tables are empty; a foreign key to an unpopulated table
     would make it impossible to record which model actually answered. The
     columns become foreign keys when the registry tables become the source of
     truth.
  2. **Messages are immutable once finalized**, enforced by a trigger rather than
     a convention. An assistant row is inserted as ``streaming`` and updated once
     when it completes; after that the database refuses to change it, so a bug
     cannot quietly rewrite what a model said.
  3. **The sequence number comes from an atomic counter on the conversation**, not
     from ``max(sequence) + 1``. Two browser tabs sending at the same moment
     serialize on that single row update and get distinct sequences instead of
     colliding on the unique constraint.
  4. **``chat.citations`` is not created yet.** It belongs to retrieval (Phase 6),
     and a table nothing writes is a table nobody maintains.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "janus_app"

TENANT_TABLES = ("chat.conversations", "chat.messages", "chat.attachments")

#: Statuses a message row may hold. Kept as a check constraint rather than an
#: enum: these evolve with the product, and widening a check is cheaper than
#: adding an enum value that can never be removed.
MESSAGE_STATUSES = ("streaming", "complete", "error", "cancelled")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS chat")

    op.execute("CREATE TYPE message_role AS ENUM ('system','user','assistant','tool')")

    _create_tables()
    _create_immutability_trigger()
    _grant_app_role()
    _enable_row_level_security()

    op.execute(
        """
        INSERT INTO core.schema_metadata (key, value, phase)
        VALUES ('schema_version', '0002', 2)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value, phase = excluded.phase
        """
    )


def _create_tables() -> None:
    op.execute(
        """
        CREATE TABLE chat.conversations (
          id              TEXT PRIMARY KEY,
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          user_id         TEXT NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
          title           TEXT,
          agent_id        TEXT,
          mode            execution_mode,
          classification  classification,
          pinned_model    TEXT,
          message_count   INTEGER NOT NULL DEFAULT 0,
          last_message_at TIMESTAMPTZ,
          metadata        JSONB NOT NULL DEFAULT '{}',
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at      TIMESTAMPTZ
        )
        """
    )
    # The conversation list is the most frequent read in the product: one user's
    # most recent threads. Partial on deleted_at so soft-deleted rows cost nothing.
    op.execute(
        """
        CREATE INDEX ix_conversations_owner_recent
          ON chat.conversations (organization_id, user_id, last_message_at DESC NULLS LAST)
          WHERE deleted_at IS NULL
        """
    )

    statuses = ", ".join(f"'{status}'" for status in MESSAGE_STATUSES)
    op.execute(
        f"""
        CREATE TABLE chat.messages (
          id                  TEXT PRIMARY KEY,
          conversation_id     TEXT NOT NULL
                              REFERENCES chat.conversations(id) ON DELETE CASCADE,
          organization_id     TEXT NOT NULL
                              REFERENCES core.organizations(id) ON DELETE CASCADE,
          role                message_role NOT NULL,
          sequence            INTEGER NOT NULL,
          content             JSONB NOT NULL DEFAULT '[]',
          status              TEXT NOT NULL DEFAULT 'complete'
                              CHECK (status IN ({statuses})),
          model_slug          TEXT,
          deployment_key      TEXT,
          provider            TEXT,
          privacy             TEXT,
          fallback_used       BOOLEAN NOT NULL DEFAULT false,
          routing_explanation TEXT,
          request_id          TEXT,
          input_tokens        INTEGER,
          output_tokens       INTEGER,
          cost_usd            NUMERIC(18,8),
          finish_reason       TEXT,
          error               JSONB,
          parent_message_id   TEXT REFERENCES chat.messages(id) ON DELETE SET NULL,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at        TIMESTAMPTZ,
          UNIQUE (conversation_id, sequence)
        )
        """
    )
    op.execute("CREATE INDEX ix_messages_thread ON chat.messages (conversation_id, sequence)")

    op.execute(
        """
        CREATE TABLE chat.attachments (
          id              TEXT PRIMARY KEY,
          organization_id TEXT NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
          conversation_id TEXT REFERENCES chat.conversations(id) ON DELETE CASCADE,
          message_id      TEXT REFERENCES chat.messages(id) ON DELETE CASCADE,
          uploaded_by     TEXT NOT NULL REFERENCES core.users(id),
          filename        TEXT NOT NULL,
          mime_type       TEXT NOT NULL,
          size_bytes      BIGINT NOT NULL,
          storage_key     TEXT NOT NULL,
          checksum_sha256 TEXT,
          classification  classification NOT NULL DEFAULT 'INTERNAL',
          scan_status     TEXT NOT NULL DEFAULT 'pending',
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Uploads arrive before the message that will carry them, so the common query
    # is "everything still unattached for this conversation".
    op.execute(
        """
        CREATE INDEX ix_attachments_pending
          ON chat.attachments (organization_id, conversation_id)
          WHERE message_id IS NULL
        """
    )
    op.execute("CREATE INDEX ix_attachments_message ON chat.attachments (message_id)")


def _create_immutability_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION chat.reject_finalized_message_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status <> 'streaming' THEN
            RAISE EXCEPTION 'message % is finalized and cannot be modified', OLD.id
              USING ERRCODE = 'restrict_violation';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER messages_immutable_once_final
          BEFORE UPDATE ON chat.messages
          FOR EACH ROW EXECUTE FUNCTION chat.reject_finalized_message_change()
        """
    )


def _grant_app_role() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA chat TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA chat TO {APP_ROLE}")
    # History is soft-deleted, never erased by a request path. Retention jobs run
    # with their own role, so the service cannot destroy a transcript even if a
    # bug tries to.
    op.execute(f"REVOKE DELETE ON chat.messages FROM {APP_ROLE}")


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
    """For local development. Production rollback is a forward fix."""
    op.execute("DROP SCHEMA IF EXISTS chat CASCADE")
    op.execute("DROP TYPE IF EXISTS message_role")
    op.execute(
        """
        UPDATE core.schema_metadata
        SET value = '0001', phase = 1
        WHERE key = 'schema_version'
        """
    )
