from __future__ import annotations

from functools import lru_cache

from janus_core.config import BaseServiceSettings
from pydantic import Field


class ApiSettings(BaseServiceSettings):
    service_name: str = "janus-api"

    # Application role: least privilege, subject to row-level security.
    database_url: str = "postgresql+asyncpg://janus_app:janus_app@localhost:5432/janus"
    # Owner role: DDL only, used by migrations and never by the running service.
    migration_database_url: str | None = None

    database_pool_size: int = 10
    database_max_overflow: int = 5
    database_statement_timeout_ms: int = 15_000

    gateway_url: str = "http://localhost:8081"
    gateway_service_token: str = ""
    gateway_timeout_seconds: float = 120.0

    session_ttl_hours: int = 24 * 14
    session_cookie_name: str = "janus_session"
    session_cookie_secure: bool = True
    session_cookie_domain: str | None = None

    # Argon2id parameters. Defaults follow OWASP guidance for interactive login;
    # tuned down only for tests, never for production.
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4

    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Attachments. Phase 2 stores bytes on a filesystem; the S3-backed store
    # arrives with the AWS infrastructure phase, behind the same interface.
    attachment_root: str = "/var/lib/janus/attachments"
    attachment_max_bytes: int = 20 * 1024 * 1024
    attachment_max_per_message: int = 10

    @property
    def effective_migration_url(self) -> str:
        return self.migration_database_url or self.database_url


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
