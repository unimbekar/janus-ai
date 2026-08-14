"""ORM models for the ``core`` and ``chat`` schemas.

Only the tables the control plane actually uses are mapped. The migrations create
the rest of the documented schema, so later phases add mappings rather than tables.

Column names, types, and constraints mirror docs/database.md — the document and
the migration are meant to stay readable side by side.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from janus_schemas.common import Classification, ExecutionMode
from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

CORE_SCHEMA = "core"
CHAT_SCHEMA = "chat"


class Base(DeclarativeBase):
    pass


execution_mode_enum = Enum(
    ExecutionMode,
    name="execution_mode",
    values_callable=lambda enum: [member.value for member in enum],
    create_type=False,
)
classification_enum = Enum(
    Classification,
    name="classification",
    values_callable=lambda enum: [member.value for member in enum],
    create_type=False,
)
org_role_enum = Enum(
    "owner",
    "admin",
    "member",
    "viewer",
    "billing",
    name="org_role",
    create_type=False,
)
message_role_enum = Enum(
    "system",
    "user",
    "assistant",
    "tool",
    name="message_role",
    create_type=False,
)


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = ({"schema": CORE_SCHEMA},)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False, default="free")
    default_mode: Mapped[ExecutionMode] = mapped_column(
        execution_mode_enum, nullable=False, default=ExecutionMode.AUTO
    )
    default_classification: Mapped[Classification] = mapped_column(
        classification_enum, nullable=False, default=Classification.INTERNAL
    )
    data_residency: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    members: Mapped[list[OrganizationMember]] = relationship(back_populates="organization")


class User(Base):
    __tablename__ = "users"
    __table_args__ = ({"schema": CORE_SCHEMA},)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    name: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    # NULL for SSO-only users (Phase 9). Never a plaintext password.
    password_hash: Mapped[str | None] = mapped_column(Text)
    mfa_secret_ref: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(Text, nullable=False, default="en")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = ({"schema": CORE_SCHEMA},)

    organization_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.organizations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(org_role_enum, nullable=False, default="member")
    invited_by: Mapped[str | None] = mapped_column(Text, ForeignKey(f"{CORE_SCHEMA}.users.id"))
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    organization: Mapped[Organization] = relationship(back_populates="members")
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_organization_active", "organization_id"),
        {"schema": CORE_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: Displayable head of the key, e.g. ``jsk_live_ab12``. Safe to show.
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    #: Argon2id hash of the whole key. The plaintext exists once, at creation.
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    #: Lookup index over the key's first bytes, so verification does not have to
    #: Argon2-verify every key in the organization.
    lookup_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    #: A key may only ever *narrow* the organization's execution mode.
    mode_ceiling: Mapped[ExecutionMode | None] = mapped_column(execution_mode_enum)
    rate_limit: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = ({"schema": CORE_SCHEMA},)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.organizations.id", ondelete="SET NULL")
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    ip: Mapped[str | None] = mapped_column(String)
    user_agent: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(Base):
    """Append-only. Application roles have no UPDATE or DELETE grant."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_org_created", "organization_id", "created_at"),
        {"schema": CORE_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.organizations.id", ondelete="SET NULL")
    )
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String)
    event_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SchemaVersion(Base):
    """Sanity marker so a service can assert it is talking to a schema it knows."""

    __tablename__ = "schema_metadata"
    __table_args__ = ({"schema": CORE_SCHEMA},)

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Conversation(Base):
    """A thread. Soft-deleted, so a mis-click is recoverable by support."""

    __tablename__ = "conversations"
    __table_args__ = ({"schema": CHAT_SCHEMA},)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[str | None] = mapped_column(Text)
    #: Narrowing only. A conversation may be more restrictive than the
    #: organization default, never less.
    mode: Mapped[ExecutionMode | None] = mapped_column(execution_mode_enum)
    classification: Mapped[Classification | None] = mapped_column(classification_enum)
    #: Model slug the user chose, or NULL for automatic selection per message.
    pinned_model: Mapped[str | None] = mapped_column(Text)
    #: Doubles as the sequence allocator: incrementing it hands out the next slot.
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conversation_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    """One turn.

    Immutable once ``status`` leaves ``streaming`` — a database trigger refuses
    the update, so the transcript cannot be rewritten after the fact.

    Attribution is stored as the gateway's own identifiers rather than as foreign
    keys into ``registry``: the catalog is configuration-as-code until Phase 3, and
    a model that has been retired from the fleet must still be nameable as the one
    that produced an old answer.
    """

    __tablename__ = "messages"
    __table_args__ = ({"schema": CHAT_SCHEMA},)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CHAT_SCHEMA}.conversations.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(message_role_enum, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Content parts, e.g. ``[{"type": "text", "text": "…"}]``. A list from the
    #: start because images and file references join it in later phases, and
    #: widening TEXT to JSONB on a hot table later is the expensive path.
    content: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="complete")
    model_slug: Mapped[str | None] = mapped_column(Text)
    deployment_key: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    privacy: Mapped[str | None] = mapped_column(Text)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    routing_explanation: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    finish_reason: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict | None] = mapped_column(JSONB)
    #: Set when this message replaces another: regeneration keeps both.
    parent_message_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey(f"{CHAT_SCHEMA}.messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attachment(Base):
    """An uploaded file.

    Phase 2 stores and serves bytes; it does not parse them. ``scan_status`` stays
    ``pending`` until malware scanning lands, and nothing downstream may treat a
    pending file as safe to open.
    """

    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_message_lookup", "message_id"),
        {"schema": CHAT_SCHEMA},
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.organizations.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey(f"{CHAT_SCHEMA}.conversations.id", ondelete="CASCADE")
    )
    #: NULL until the message that carries the file is sent.
    message_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey(f"{CHAT_SCHEMA}.messages.id", ondelete="CASCADE")
    )
    uploaded_by: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{CORE_SCHEMA}.users.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Opaque key in whichever object store is configured. Never a URL, and never
    #: anything a client is allowed to construct.
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(Text)
    classification: Mapped[Classification] = mapped_column(
        classification_enum, nullable=False, default=Classification.INTERNAL
    )
    scan_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
