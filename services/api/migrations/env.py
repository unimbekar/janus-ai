"""Alembic environment.

Migrations connect with the **owner** role (DDL), while the service connects with
a restricted role that is subject to row-level security. Keeping them separate is
what makes the RLS tests meaningful.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from api_app.models import Base
from api_app.settings import get_settings
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.effective_migration_url)

VERSION_TABLE_SCHEMA = "public"


def include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Autogenerate ignores objects Janus manages by hand.

    Row-level security policies, grants, and functions are written explicitly in
    migrations; autogenerate does not see them and must not try to drop them.
    """
    return not (
        type_ == "schema" and name in {"registry", "telemetry", "chat", "agent", "knowledge"}
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        version_table_schema=VERSION_TABLE_SCHEMA,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        version_table_schema=VERSION_TABLE_SCHEMA,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
