"""Base settings shared by all services.

Configuration is always injected through the environment with the ``JANUS_``
prefix. No literals in code, no secrets in the repository: an unset required
value fails at startup rather than at first use.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"
    TEST = "test"


class BaseServiceSettings(BaseSettings):
    """Common settings; each service subclasses and adds its own."""

    model_config = SettingsConfigDict(
        env_prefix="JANUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    service_name: str = "janus"
    log_level: str = "INFO"

    # Telemetry: unset endpoint means tracing is a no-op, which is the
    # expected local-development state.
    otel_exporter_otlp_endpoint: str | None = None
    otel_traces_sampler_ratio: float = 1.0

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PROD

    @property
    def is_local(self) -> bool:
        return self.environment in (Environment.LOCAL, Environment.TEST)
