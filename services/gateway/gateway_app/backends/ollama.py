"""Ollama adapter — local development and offline mode.

Ollama exposes an OpenAI-compatible surface at ``/v1``, so this adapter is thin.
It exists as its own class because the differences are real: no authentication,
models must be pulled before use, and a cold model can take tens of seconds to
load, which the health probe reports as ``warming`` rather than a failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import httpx
from janus_schemas.common import HealthState, Protocol
from janus_schemas.embeddings import EmbeddingRequest, EmbeddingResponse

from gateway_app.backends.base import CallContext, HealthReport
from gateway_app.backends.openai_compatible import OpenAICompatibleBackend

if TYPE_CHECKING:  # pragma: no cover
    from gateway_app.registry.records import DeploymentRecord


class OllamaBackend(OpenAICompatibleBackend):
    backend_id = "ollama"
    protocol = Protocol.OPENAI_COMPATIBLE

    # Ollama's OpenAI surface omits usage on streamed responses, so the gateway
    # estimates token counts and marks them estimated rather than reporting
    # numbers it did not measure.
    supports_stream_usage = False

    runtime_capability_overrides: ClassVar[dict[str, bool]] = {
        "streaming": True,
        # Tool calling depends on the loaded model; declared per model in the
        # registry rather than assumed for the runtime.
        "structured_output": False,
    }

    async def health(self, deployment: DeploymentRecord) -> HealthReport:
        report = await super().health(deployment)
        if report.state is not HealthState.READY or not deployment.endpoint:
            return report

        # A model that is present but not resident still serves — slowly. Report
        # it as warming so the router deprioritizes rather than excludes it.
        try:
            tags_url = deployment.endpoint.rstrip("/").removesuffix("/v1") + "/api/ps"
            response = await self._client.get(tags_url, timeout=httpx.Timeout(3.0, connect=2.0))
            if response.status_code == 200:
                loaded = {
                    entry.get("name", "") for entry in response.json().get("models", []) or []
                }
                if not any(name.startswith(deployment.upstream_model_id) for name in loaded):
                    return HealthReport(
                        HealthState.WARMING,
                        report.latency_ms,
                        "model_not_resident",
                    )
        except Exception:
            return report

        return report

    async def embeddings(
        self,
        request: EmbeddingRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> EmbeddingResponse:
        if not deployment.capability_overrides.get("embeddings", False):
            self.unsupported("embeddings", deployment)
        return await super().embeddings(request, deployment, ctx)
