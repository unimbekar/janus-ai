from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from janus_core.config import BaseServiceSettings
from pydantic import Field


class GatewaySettings(BaseServiceSettings):
    service_name: str = "janus-gateway"

    # Registry-as-code: the catalog is files in Git, applied per environment.
    registry_dir: Path = Field(default=Path("registry"))

    # Callers are internal in Phase 1 (the control plane and workers). Public
    # OpenAI-compatible access arrives in Phase 3 with per-organization keys.
    gateway_service_token: str = Field(default="", min_length=0)

    request_timeout_seconds: float = 120.0
    connect_timeout_seconds: float = 5.0
    first_token_timeout_seconds: float = 30.0

    health_probe_interval_seconds: float = 30.0
    health_probe_enabled: bool = True
    unhealthy_failure_threshold: int = 3

    # Local development default; overridden per deployment in the registry.
    ollama_base_url: str = "http://localhost:11434/v1"

    max_fallback_attempts: int = 3


@lru_cache
def get_settings() -> GatewaySettings:
    return GatewaySettings()
