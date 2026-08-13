"""The backend interface — the mechanism that makes provider independence real.

Every model runtime, cloud or local, implements ``ModelBackend``. Nothing above
this layer may know which vendor served a request, and no provider SDK may be
imported outside this package (enforced by the import-linter contracts in the
root ``pyproject.toml``).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from janus_core.errors import JanusError
from janus_schemas.chat import ChatChunk, ChatCompletionRequest, ChatCompletionResponse
from janus_schemas.common import Classification, ExecutionMode, HealthState, Protocol
from janus_schemas.embeddings import EmbeddingRequest, EmbeddingResponse
from janus_schemas.models import ModelCapabilities

if TYPE_CHECKING:  # pragma: no cover
    from gateway_app.registry.records import DeploymentRecord


class UnsupportedCapabilityError(JanusError):
    """The backend cannot do what was asked, and says so instead of degrading.

    Silent degradation is forbidden: the router needs a truthful answer so it can
    pick a different deployment.
    """

    error_type = "invalid_request"
    code = "capability_unsupported"
    http_status = 400


@dataclass(slots=True)
class CallContext:
    """Per-call context handed to a backend.

    Carries correlation and budget, never end-user identity beyond what a
    provider legitimately needs.
    """

    request_id: str
    organization_id: str | None = None
    classification: Classification = Classification.INTERNAL
    mode: ExecutionMode = ExecutionMode.AUTO
    timeout_seconds: float = 120.0
    first_token_timeout_seconds: float = 30.0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.timeout_seconds - (time.monotonic() - self.started_at))


@dataclass(slots=True)
class HealthReport:
    """Result of probing one deployment."""

    state: HealthState
    latency_ms: int | None = None
    detail: str | None = None
    checked_at: float = field(default_factory=time.time)

    @property
    def is_routable(self) -> bool:
        return self.state.is_routable


class ModelBackend(ABC):
    """One implementation per provider or inference runtime."""

    backend_id: str
    protocol: Protocol

    @abstractmethod
    async def generate(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> ChatCompletionResponse:
        """Produce a complete response."""

    @abstractmethod
    def stream(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> AsyncIterator[ChatChunk]:
        """Produce ordered chunks. Cancellation must close the upstream call."""

    @abstractmethod
    async def embeddings(
        self,
        request: EmbeddingRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> EmbeddingResponse:
        """Produce embeddings, or raise ``UnsupportedCapabilityError``."""

    @abstractmethod
    async def health(self, deployment: DeploymentRecord) -> HealthReport:
        """Probe the deployment. Must not raise; report ``OFFLINE`` instead."""

    @abstractmethod
    async def capabilities(self, deployment: DeploymentRecord) -> ModelCapabilities:
        """What this runtime can actually do for this deployment."""

    async def aclose(self) -> None:
        """Release connections. Default is a no-op."""
        return None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} backend_id={self.backend_id!r}>"
