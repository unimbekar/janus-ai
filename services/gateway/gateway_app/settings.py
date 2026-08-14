from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from janus_core.config import BaseServiceSettings
from pydantic import Field


class GatewaySettings(BaseServiceSettings):
    service_name: str = "janus-gateway"

    registry_dir: Path = Field(default=Path("registry"))

    # Internal callers (control plane) use a service token + X-Janus-* headers.
    gateway_service_token: str = Field(default="", min_length=0)

    # Public OpenAI-compatible clients authenticate with organization API keys (jsk_…).
    public_api_enabled: bool = True

    # Optional Postgres for durable routing decisions and usage records.
    database_url: str = ""

    # Optional Redis for shared rate limits and health cache.
    redis_url: str = ""
    rate_limit_per_minute: int = 120

    request_timeout_seconds: float = 120.0
    connect_timeout_seconds: float = 5.0
    first_token_timeout_seconds: float = 30.0

    health_probe_interval_seconds: float = 30.0
    health_probe_enabled: bool = True
    unhealthy_failure_threshold: int = 3

    ollama_base_url: str = "http://localhost:11434/v1"

    max_fallback_attempts: int = 3


@lru_cache
def get_settings() -> GatewaySettings:
    return GatewaySettings()
