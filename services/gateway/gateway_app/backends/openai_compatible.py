"""Base adapter for runtimes that already speak the OpenAI chat protocol.

Covers vLLM, SGLang, Ollama, llama.cpp's server, and OpenAI itself. Specific
adapters subclass this and override authentication, URL shape, or quirks rather
than reimplementing transport and error mapping.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from janus_core.errors import ProviderError, RateLimitError, TimeoutError, ValidationError
from janus_core.logging import get_logger
from janus_schemas.chat import ChatChunk, ChatCompletionRequest, ChatCompletionResponse
from janus_schemas.common import HealthState, Protocol
from janus_schemas.embeddings import EmbeddingRequest, EmbeddingResponse
from janus_schemas.models import ModelCapabilities

from gateway_app.backends.base import (
    CallContext,
    HealthReport,
    ModelBackend,
    UnsupportedCapabilityError,
)
from gateway_app.backends.credentials import resolve_credential

if TYPE_CHECKING:  # pragma: no cover
    from gateway_app.registry.records import DeploymentRecord

logger = get_logger(__name__)

_SSE_DATA_PREFIX = "data:"
_SSE_DONE = "[DONE]"

# Fields Janus owns and never forwards upstream.
_STRIPPED_FIELDS = frozenset({"janus", "model", "stream", "stream_options"})


class OpenAICompatibleBackend(ModelBackend):
    backend_id = "openai_compatible"
    protocol = Protocol.OPENAI_COMPATIBLE

    #: Whether the runtime honors ``stream_options.include_usage``. When it does
    #: not, the caller estimates usage and marks it as estimated.
    supports_stream_usage: bool = True

    #: Declared runtime abilities; subclasses narrow these where a runtime is
    #: known to be weaker, so the router stops offering what cannot be done.
    runtime_capability_overrides: ClassVar[dict[str, bool]] = {}

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    # ------------------------------------------------------------------ wire

    def _url(self, deployment: DeploymentRecord, path: str) -> str:
        if not deployment.endpoint:
            raise ProviderError(
                "Deployment has no endpoint configured.",
                code="deployment_misconfigured",
                details={"deployment": deployment.key},
            )
        return f"{deployment.endpoint.rstrip('/')}/{path.lstrip('/')}"

    def _headers(self, deployment: DeploymentRecord, ctx: CallContext) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Request-Id": ctx.request_id,
        }
        token = resolve_credential(deployment.credentials_ref)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _payload(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        payload = request.model_dump(exclude_none=True, exclude=set(_STRIPPED_FIELDS))
        payload["model"] = deployment.upstream_model_id
        payload["stream"] = stream
        if stream and self.supports_stream_usage:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _raise_for_status(self, response: httpx.Response, deployment: DeploymentRecord) -> None:
        if response.status_code < 400:
            return

        # Bodies can contain provider-specific detail; keep only a short, safe
        # summary and never echo credentials or internal endpoints.
        summary = response.text[:200] if response.text else ""
        context = {"deployment": deployment.key, "upstream_status": response.status_code}

        if response.status_code in (401, 403):
            raise ProviderError(
                "Upstream model provider rejected our credentials.",
                code="provider_auth_failed",
                details=context,
                retryable=False,
            )
        if response.status_code == 404:
            raise ProviderError(
                "Upstream model is not available at this deployment.",
                code="provider_model_missing",
                details=context,
                retryable=False,
            )
        if response.status_code == 429:
            raise RateLimitError(
                "Upstream model provider is rate limiting this request.",
                code="provider_rate_limited",
                details=context,
            )
        if response.status_code == 400 and "context" in summary.lower():
            raise ValidationError(
                "The request exceeds the context window of the selected model.",
                code="context_length_exceeded",
                details=context,
            )
        if response.status_code < 500:
            raise ProviderError(
                "Upstream model provider rejected the request.",
                code="provider_bad_request",
                details=context,
                retryable=False,
            )
        raise ProviderError(
            "Upstream model provider failed.",
            code="provider_unavailable",
            details=context,
        )

    # -------------------------------------------------------------- interface

    async def generate(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> ChatCompletionResponse:
        try:
            response = await self._client.post(
                self._url(deployment, "chat/completions"),
                json=self._payload(request, deployment, stream=False),
                headers=self._headers(deployment, ctx),
                timeout=httpx.Timeout(ctx.remaining_seconds, connect=5.0),
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError(
                "The model did not respond within the request budget.",
                details={"deployment": deployment.key},
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Could not reach the model runtime.",
                code="provider_unreachable",
                details={"deployment": deployment.key},
            ) from exc

        self._raise_for_status(response, deployment)
        return self._parse_response(response.json(), deployment)

    def _parse_response(
        self, body: dict[str, Any], deployment: DeploymentRecord
    ) -> ChatCompletionResponse:
        # Present the Janus model slug, not the upstream identifier: callers
        # must never learn provider-internal naming.
        body["model"] = deployment.model_slug
        body.setdefault("object", "chat.completion")
        try:
            return ChatCompletionResponse.model_validate(body)
        except ValueError as exc:
            raise ProviderError(
                "Upstream returned a response Janus could not parse.",
                code="provider_bad_response",
                details={"deployment": deployment.key},
            ) from exc

    async def stream(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> AsyncIterator[ChatChunk]:
        url = self._url(deployment, "chat/completions")
        payload = self._payload(request, deployment, stream=True)
        headers = self._headers(deployment, ctx)

        try:
            async with self._client.stream(
                "POST",
                url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(ctx.remaining_seconds, connect=5.0),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    self._raise_for_status(response, deployment)

                async for line in response.aiter_lines():
                    chunk = self._parse_stream_line(line, deployment)
                    if chunk is not None:
                        yield chunk
        except httpx.TimeoutException as exc:
            raise TimeoutError(
                "The model stopped responding mid-stream.",
                details={"deployment": deployment.key},
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Lost the connection to the model runtime.",
                code="provider_unreachable",
                details={"deployment": deployment.key},
            ) from exc

    def _parse_stream_line(self, line: str, deployment: DeploymentRecord) -> ChatChunk | None:
        line = line.strip()
        if not line or not line.startswith(_SSE_DATA_PREFIX):
            return None

        data = line[len(_SSE_DATA_PREFIX) :].strip()
        if data == _SSE_DONE:
            return None

        try:
            body = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("upstream_chunk_unparseable", extra={"deployment": deployment.key})
            return None

        body["model"] = deployment.model_slug
        body.setdefault("object", "chat.completion.chunk")
        body.setdefault("choices", [])
        try:
            return ChatChunk.model_validate(body)
        except ValueError:
            logger.warning("upstream_chunk_invalid", extra={"deployment": deployment.key})
            return None

    async def embeddings(
        self,
        request: EmbeddingRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> EmbeddingResponse:
        payload = request.model_dump(exclude_none=True, exclude={"janus", "model"})
        payload["model"] = deployment.upstream_model_id
        try:
            response = await self._client.post(
                self._url(deployment, "embeddings"),
                json=payload,
                headers=self._headers(deployment, ctx),
                timeout=httpx.Timeout(ctx.remaining_seconds, connect=5.0),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Could not reach the embedding runtime.",
                code="provider_unreachable",
                details={"deployment": deployment.key},
            ) from exc

        self._raise_for_status(response, deployment)
        body = response.json()
        body["model"] = deployment.model_slug
        try:
            return EmbeddingResponse.model_validate(body)
        except ValueError as exc:
            raise ProviderError(
                "Upstream returned embeddings Janus could not parse.",
                code="provider_bad_response",
                details={"deployment": deployment.key},
            ) from exc

    async def health(self, deployment: DeploymentRecord) -> HealthReport:
        """Probe the runtime. Never raises — an unreachable runtime is data."""
        try:
            response = await self._client.get(
                self._url(deployment, "models"),
                headers={"X-Request-Id": "health-probe"},
                timeout=httpx.Timeout(5.0, connect=2.0),
            )
        except Exception as exc:
            return HealthReport(state=HealthState.OFFLINE, detail=type(exc).__name__)

        latency_ms = int(response.elapsed.total_seconds() * 1000)
        if response.status_code >= 500:
            return HealthReport(HealthState.DEGRADED, latency_ms, f"http_{response.status_code}")
        if response.status_code >= 400:
            # Reachable but refusing us: an auth or configuration problem.
            return HealthReport(HealthState.OFFLINE, latency_ms, f"http_{response.status_code}")
        return HealthReport(HealthState.READY, latency_ms)

    async def capabilities(self, deployment: DeploymentRecord) -> ModelCapabilities:
        overrides = {**self.runtime_capability_overrides, **deployment.capability_overrides}
        return ModelCapabilities(streaming=True).model_copy(update=overrides)

    def unsupported(self, capability: str, deployment: DeploymentRecord) -> None:
        raise UnsupportedCapabilityError(
            f"This deployment does not support {capability}.",
            details={"capability": capability, "deployment": deployment.key},
        )
