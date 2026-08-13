"""Backend construction.

Adding a provider means adding an adapter here and a registry entry — nothing
above this package changes. That is the whole point of the abstraction.
"""

from __future__ import annotations

import httpx
from janus_core.errors import JanusError

from gateway_app.backends.base import CallContext, HealthReport, ModelBackend
from gateway_app.backends.mock import MockBackend
from gateway_app.backends.ollama import OllamaBackend
from gateway_app.backends.openai_compatible import OpenAICompatibleBackend

__all__ = [
    "BackendRegistry",
    "CallContext",
    "HealthReport",
    "MockBackend",
    "ModelBackend",
    "OllamaBackend",
    "OpenAICompatibleBackend",
    "UnknownBackendError",
]


class UnknownBackendError(JanusError):
    error_type = "internal"
    code = "unknown_backend"
    http_status = 500


class BackendRegistry:
    """Owns one adapter instance per backend id, plus their shared HTTP client."""

    def __init__(self, *, connect_timeout: float = 5.0, request_timeout: float = 120.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(request_timeout, connect=connect_timeout),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            follow_redirects=False,
        )
        self._backends: dict[str, ModelBackend] = {
            "mock": MockBackend(),
            "ollama": OllamaBackend(self._client),
            # vLLM and SGLang speak the same protocol; they are registered in
            # Phase 8 when Janus GPU serving lands, with their own adapters for
            # runtime-specific health and warming behavior.
            "openai_compatible": OpenAICompatibleBackend(self._client),
        }

    def get(self, backend_id: str) -> ModelBackend:
        backend = self._backends.get(backend_id)
        if backend is None:
            raise UnknownBackendError(
                "No adapter is registered for this deployment's backend.",
                details={"backend": backend_id, "known": sorted(self._backends)},
            )
        return backend

    def known_backends(self) -> list[str]:
        return sorted(self._backends)

    async def aclose(self) -> None:
        for backend in self._backends.values():
            await backend.aclose()
        await self._client.aclose()
