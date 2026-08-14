"""Public API key authentication for the Model Gateway."""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from janus_core.errors import AuthenticationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway_app.db import GatewayDatabase

_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


@dataclass(frozen=True, slots=True)
class ApiKeyIdentity:
    id: str
    organization_id: str
    scopes: tuple[str, ...]
    mode_ceiling: str | None


def api_key_lookup_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ApiKeyAuthenticator:
    def __init__(self, db: GatewayDatabase) -> None:
        self._db = db
        self._hasher = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=1)

    async def authenticate(self, key: str) -> ApiKeyIdentity:
        lookup = api_key_lookup_hash(key)
        async with self._db.auth_session() as session:
            row = await self._fetch_row(session, lookup)
            if row is None:
                self._hasher.hash(key)
                raise AuthenticationError("API key is not valid.", code="invalid_api_key")
            if not self._verify(row["key_hash"], key):
                raise AuthenticationError("API key is not valid.", code="invalid_api_key")
            if row["revoked_at"] is not None:
                raise AuthenticationError("API key has been revoked.", code="api_key_revoked")
            return ApiKeyIdentity(
                id=row["id"],
                organization_id=row["organization_id"],
                scopes=tuple(row["scopes"] or ()),
                mode_ceiling=row["mode_ceiling"],
            )

    async def _fetch_row(self, session: AsyncSession, lookup: str) -> dict | None:
        result = await session.execute(
            text(
                """
                SELECT id, organization_id, key_hash, scopes, mode_ceiling, revoked_at
                FROM core.authenticate_api_key(:lookup)
                """
            ),
            {"lookup": lookup},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    def _verify(self, key_hash: str, key: str) -> bool:
        try:
            return self._hasher.verify(key_hash, key)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
                self._hasher.verify(_DUMMY_HASH, key)
            return False
