"""Database access and tenant scoping.

Multi-tenancy is enforced in the database, not in application code (ADR 0005).
Every request runs in a transaction that sets ``janus.organization_id``, and
row-level security policies compare each row against it. A repository that
forgets a ``WHERE organization_id = …`` clause therefore returns nothing rather
than another tenant's data.

The service connects as a role without ``BYPASSRLS``, and the tables are marked
``FORCE ROW LEVEL SECURITY``, so the policies apply even to the table owner.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from janus_core.errors import JanusError
from janus_core.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = get_logger(__name__)

TENANT_SETTING = "janus.organization_id"
ACTOR_SETTING = "janus.user_id"

# The schema this build of the service knows how to talk to. Bumped by whichever
# migration changes a table this code reads or writes.
EXPECTED_SCHEMA_VERSION = "0001"


class TenantContextError(JanusError):
    """A tenant-scoped query was attempted without an organization context."""

    error_type = "internal"
    code = "tenant_context_missing"
    http_status = 500


async def set_scope(
    session: AsyncSession,
    *,
    organization_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Set the tenant and actor scope for the remainder of this transaction.

    ``set_config(..., is_local => true)`` is the parameterizable form of
    ``SET LOCAL``: scoped to the transaction, so a pooled connection cannot carry
    one request's tenant context into the next.

    Calling this mid-transaction is legitimate and necessary in one case:
    creating an organization. The identifier is generated in application code, so
    the scope can be set before the first insert and the whole creation stays in
    one transaction.
    """
    if organization_id is not None:
        await session.execute(
            text("SELECT set_config(:setting, :value, true)"),
            {"setting": TENANT_SETTING, "value": organization_id},
        )
    if user_id is not None:
        await session.execute(
            text("SELECT set_config(:setting, :value, true)"),
            {"setting": ACTOR_SETTING, "value": user_id},
        )


def create_engine(
    url: str, *, pool_size: int, max_overflow: int, statement_timeout_ms: int
) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "statement_timeout": str(statement_timeout_ms),
                "application_name": "janus-api",
            }
        },
    )


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(
        self, organization_id: str | None = None, user_id: str | None = None
    ) -> AsyncIterator[AsyncSession]:
        """Open a transaction, optionally scoped to an organization and a user.

        Both settings are unset by default, which makes tenant-scoped tables
        invisible — the right state for genuinely cross-tenant work such as
        sign-in by email address.

        ``user_id`` exists because membership is inherently cross-tenant: a user
        must be able to list the organizations they belong to before choosing
        one. The membership policy accepts either scope, so that query needs no
        exception to row-level security.
        """
        async with self._sessionmaker() as session, session.begin():
            await set_scope(session, organization_id=organization_id, user_id=user_id)
            yield session

    async def healthy(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.warning("database_unreachable", exc_info=True)
            return False

    async def schema_version(self) -> str | None:
        """The schema version recorded by migrations, or ``None`` if unreadable.

        A reachable database with no schema is the normal state before the first
        migration runs, and it is worth distinguishing: every request would fail
        on a missing table, so the service is connected but not ready.
        """
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(
                    text("SELECT value FROM core.schema_metadata WHERE key = 'schema_version'")
                )
                return result.scalar_one_or_none()
        except Exception:
            logger.warning("schema_version_unreadable", exc_info=True)
            return None

    async def aclose(self) -> None:
        await self._engine.dispose()
