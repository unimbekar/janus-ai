"""Minimal database access for gateway telemetry writes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str, *, pool_size: int = 5, max_overflow: int = 5) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, max_overflow=max_overflow)


class GatewayDatabase:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(
        self, *, organization_id: str, user_id: str | None = None, api_key_id: str | None = None
    ) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text("SELECT set_config('janus.organization_id', :value, true)"),
                {"value": organization_id},
            )
            if user_id:
                await session.execute(
                    text("SELECT set_config('janus.user_id', :value, true)"),
                    {"value": user_id},
                )
            if api_key_id:
                await session.execute(
                    text("SELECT set_config('janus.api_key_id', :value, true)"),
                    {"value": api_key_id},
                )
            yield session

    async def aclose(self) -> None:
        await self._engine.dispose()

    @asynccontextmanager
    async def auth_session(self) -> AsyncIterator[AsyncSession]:
        """Session without tenant scope — API key authentication only."""
        async with self._sessionmaker() as session:
            yield session
