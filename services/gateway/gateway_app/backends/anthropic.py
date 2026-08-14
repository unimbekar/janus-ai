"""Anthropic Messages API adapter (native protocol)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from janus_core.errors import ProviderError, RateLimitError, TimeoutError
from janus_core.logging import get_logger
from janus_schemas.chat import (
    ChatChoice,
    ChatChunk,
    ChatChunkChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatDelta,
    ChatMessage,
    Usage,
)
from janus_schemas.common import HealthState, Protocol, Role
from janus_schemas.embeddings import EmbeddingRequest, EmbeddingResponse
from janus_schemas.models import ModelCapabilities

from gateway_app.backends.base import (
    CallContext,
    HealthReport,
    ModelBackend,
    UnsupportedCapabilityError,
)
from gateway_app.backends.credentials import resolve_credential
from gateway_app.registry.records import DeploymentRecord

logger = get_logger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicBackend(ModelBackend):
    backend_id = "anthropic"
    protocol = Protocol.NATIVE

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def _headers(self, deployment: DeploymentRecord, ctx: CallContext) -> dict[str, str]:
        token = resolve_credential(deployment.credentials_ref)
        if not token:
            raise ProviderError(
                "Anthropic credential is not configured.", code="provider_auth_failed"
            )
        return {
            "Content-Type": "application/json",
            "x-api-key": token,
            "anthropic-version": _ANTHROPIC_VERSION,
            "X-Request-Id": ctx.request_id,
        }

    def _url(self, deployment: DeploymentRecord) -> str:
        base = (deployment.endpoint or "https://api.anthropic.com").rstrip("/")
        return f"{base}/v1/messages"

    def _payload(
        self, request: ChatCompletionRequest, deployment: DeploymentRecord
    ) -> dict[str, Any]:
        system_parts: list[str] = []
        messages: list[dict[str, str]] = []
        for message in request.messages:
            if message.role == "system":
                if isinstance(message.content, str):
                    system_parts.append(message.content)
                continue
            role = "assistant" if message.role == "assistant" else "user"
            content = message.content if isinstance(message.content, str) else str(message.content)
            messages.append({"role": role, "content": content})
        body: dict[str, Any] = {
            "model": deployment.upstream_model_id,
            "max_tokens": request.max_tokens or 4096,
            "messages": messages,
            "stream": request.stream,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if request.temperature is not None:
            body["temperature"] = request.temperature
        return body

    async def generate(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> ChatCompletionResponse:
        response = await self._client.post(
            self._url(deployment),
            headers=self._headers(deployment, ctx),
            json={**self._payload(request, deployment), "stream": False},
            timeout=ctx.remaining_seconds,
        )
        self._raise_for_status(response)
        data = response.json()
        text = _extract_text(data)
        usage = Usage(
            prompt_tokens=data.get("usage", {}).get("input_tokens", 0),
            completion_tokens=data.get("usage", {}).get("output_tokens", 0),
        )
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        return ChatCompletionResponse(
            id=f"chatcmpl_anthropic_{ctx.request_id[-12:]}",
            created=int(__import__("time").time()),
            model=deployment.model_slug,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChatMessage(role=Role.ASSISTANT, content=text),
                    finish_reason="stop",
                )
            ],
            usage=usage,
        )

    async def stream(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> AsyncIterator[ChatChunk]:
        async with self._client.stream(
            "POST",
            self._url(deployment),
            headers=self._headers(deployment, ctx),
            json=self._payload(request, deployment),
            timeout=ctx.remaining_seconds,
        ) as response:
            self._raise_for_status(response)
            completion_id = f"chatcmpl_anthropic_{ctx.request_id[-12:]}"
            created = int(__import__("time").time())
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                event = json.loads(payload)
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {}).get("text", "")
                    if delta:
                        yield ChatChunk(
                            id=completion_id,
                            created=created,
                            model=deployment.model_slug,
                            choices=[ChatChunkChoice(index=0, delta=ChatDelta(content=delta))],
                        )
                elif event.get("type") == "message_delta":
                    usage_data = event.get("usage", {})
                    if usage_data:
                        usage = Usage(
                            prompt_tokens=usage_data.get("input_tokens", 0),
                            completion_tokens=usage_data.get("output_tokens", 0),
                        )
                        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
                        yield ChatChunk(
                            id=completion_id,
                            created=created,
                            model=deployment.model_slug,
                            choices=[
                                ChatChunkChoice(index=0, delta=ChatDelta(), finish_reason="stop")
                            ],
                            usage=usage,
                        )

    async def embeddings(
        self,
        request: EmbeddingRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> EmbeddingResponse:
        raise UnsupportedCapabilityError("Anthropic embeddings are not supported by this adapter.")

    async def health(self, deployment: DeploymentRecord) -> HealthReport:
        if not deployment.endpoint and not deployment.credentials_ref:
            return HealthReport(state=HealthState.OFFLINE, detail="not configured")
        return HealthReport(state=HealthState.READY)

    async def capabilities(self, deployment: DeploymentRecord) -> ModelCapabilities:
        return ModelCapabilities(streaming=True, reasoning=True, tool_calling=True)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 429:
            raise RateLimitError("Anthropic rate limit exceeded.")
        if response.status_code in (408, 504):
            raise TimeoutError("Anthropic request timed out.")
        if response.status_code >= 400:
            raise ProviderError(
                "Anthropic request failed.",
                code="provider_error",
                details={"status": response.status_code},
            )


def _extract_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)
