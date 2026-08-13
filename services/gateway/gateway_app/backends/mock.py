"""Deterministic in-process backend for tests and local development.

No network, no GPU, no provider account. CI runs the full request path against
this adapter, so the pipeline, streaming, fallback, and error mapping are all
tested without touching a vendor.

Failure injection: the last user message may contain a control token, which lets
tests exercise the failure paths that matter without patching internals.

    __fail__            retryable provider failure on every deployment
    __fail_on__:<key>   retryable failure on one deployment only (fallback tests)
    __auth_fail__       non-retryable credential failure
    __ratelimit__       provider rate limit
    __timeout__         request-budget timeout
    __slow__:<seconds>  delay before the first token
    __delay__:<seconds> delay between chunks, so buffering anywhere in the path
                        is visible as chunks arriving together instead of spread out
    __tokens__:<n>      emit n chunks
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from janus_core.errors import ProviderError, RateLimitError, TimeoutError
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
from janus_schemas.embeddings import EmbeddingItem, EmbeddingRequest, EmbeddingResponse
from janus_schemas.models import ModelCapabilities

from gateway_app.backends.base import CallContext, HealthReport, ModelBackend

if TYPE_CHECKING:  # pragma: no cover
    from gateway_app.registry.records import DeploymentRecord

_SLOW_PATTERN = re.compile(r"__slow__:(\d+(?:\.\d+)?)")
_DELAY_PATTERN = re.compile(r"__delay__:(\d+(?:\.\d+)?)")
_MAX_CHUNK_DELAY_SECONDS = 1.0
_FAIL_ON_PATTERN = re.compile(r"__fail_on__:([\w.-]+)")
_TOKENS_PATTERN = re.compile(r"__tokens__:(\d+)")
_DEFAULT_CHUNKS = 8
_EMBEDDING_DIMENSIONS = 8
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Coarse token estimate used only where a runtime reports no usage.

    Deliberately crude: anything reported from an estimate is labeled estimated
    so it is never mistaken for a metered figure.
    """
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


class MockBackend(ModelBackend):
    backend_id = "mock"
    protocol = Protocol.NATIVE

    async def _apply_control_tokens(self, prompt: str, deployment: DeploymentRecord) -> None:
        context = {"deployment": deployment.key, "injected": True}

        if "__auth_fail__" in prompt:
            raise ProviderError(
                "Upstream model provider rejected our credentials.",
                code="provider_auth_failed",
                details=context,
                retryable=False,
            )
        if "__ratelimit__" in prompt:
            raise RateLimitError(
                "Upstream model provider is rate limiting this request.",
                code="provider_rate_limited",
                details=context,
            )
        if "__timeout__" in prompt:
            raise TimeoutError(
                "The model did not respond within the request budget.",
                details=context,
            )
        targeted = _FAIL_ON_PATTERN.search(prompt)
        if "__fail__" in prompt or (targeted and targeted.group(1) == deployment.key):
            raise ProviderError(
                "Upstream model provider failed.",
                code="provider_unavailable",
                details=context,
            )

        delay = _SLOW_PATTERN.search(prompt)
        if delay:
            await asyncio.sleep(float(delay.group(1)))

    @staticmethod
    def _prompt_text(request: ChatCompletionRequest) -> str:
        parts: list[str] = []
        for message in request.messages:
            if isinstance(message.content, str):
                parts.append(message.content)
            elif isinstance(message.content, list):
                parts.extend(
                    str(item.get("text", "")) for item in message.content if isinstance(item, dict)
                )
        return "\n".join(parts)

    @staticmethod
    def _reply(prompt: str, deployment: DeploymentRecord) -> str:
        """Deterministic reply: same input and deployment, same output."""
        digest = hashlib.sha256(f"{deployment.key}:{prompt}".encode()).hexdigest()[:8]
        preview = prompt.strip().splitlines()[-1][:120] if prompt.strip() else "(empty)"
        return (
            f"[mock:{deployment.key}:{digest}] Responding to: {preview} "
            "— this is a deterministic test response, not model output."
        )

    def _chunks(self, text: str, prompt: str) -> list[str]:
        requested = _TOKENS_PATTERN.search(prompt)
        count = int(requested.group(1)) if requested else _DEFAULT_CHUNKS
        count = max(1, min(count, 512))
        size = max(1, math.ceil(len(text) / count))
        return [text[index : index + size] for index in range(0, len(text), size)]

    async def generate(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> ChatCompletionResponse:
        prompt = self._prompt_text(request)
        await self._apply_control_tokens(prompt, deployment)
        reply = self._reply(prompt, deployment)

        prompt_tokens = estimate_tokens(prompt)
        completion_tokens = estimate_tokens(reply)
        return ChatCompletionResponse(
            id=f"chatcmpl_mock_{int(time.time() * 1000)}",
            created=int(time.time()),
            model=deployment.model_slug,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChatMessage(role=Role.ASSISTANT, content=reply),
                    finish_reason="stop",
                )
            ],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    async def stream(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> AsyncIterator[ChatChunk]:
        prompt = self._prompt_text(request)
        await self._apply_control_tokens(prompt, deployment)

        reply = self._reply(prompt, deployment)
        completion_id = f"chatcmpl_mock_{int(time.time() * 1000)}"
        created = int(time.time())

        yield ChatChunk(
            id=completion_id,
            created=created,
            model=deployment.model_slug,
            choices=[ChatChunkChoice(index=0, delta=ChatDelta(role=Role.ASSISTANT))],
        )

        requested_delay = _DELAY_PATTERN.search(prompt)
        chunk_delay = (
            min(float(requested_delay.group(1)), _MAX_CHUNK_DELAY_SECONDS)
            if requested_delay
            else 0.0
        )

        for piece in self._chunks(reply, prompt):
            yield ChatChunk(
                id=completion_id,
                created=created,
                model=deployment.model_slug,
                choices=[ChatChunkChoice(index=0, delta=ChatDelta(content=piece))],
            )
            # Zero still yields control, so cancellation mid-stream stays observable.
            await asyncio.sleep(chunk_delay)

        prompt_tokens = estimate_tokens(prompt)
        completion_tokens = estimate_tokens(reply)
        yield ChatChunk(
            id=completion_id,
            created=created,
            model=deployment.model_slug,
            choices=[ChatChunkChoice(index=0, delta=ChatDelta(), finish_reason="stop")],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    async def embeddings(
        self,
        request: EmbeddingRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> EmbeddingResponse:
        items: list[EmbeddingItem] = []
        total_tokens = 0
        for index, text in enumerate(request.inputs):
            digest = hashlib.sha256(text.encode()).digest()
            vector = [
                (digest[position] / 255.0) * 2 - 1 for position in range(_EMBEDDING_DIMENSIONS)
            ]
            items.append(EmbeddingItem(index=index, embedding=vector))
            total_tokens += estimate_tokens(text)

        return EmbeddingResponse(
            data=items,
            model=deployment.model_slug,
            usage=Usage(prompt_tokens=total_tokens, total_tokens=total_tokens),
        )

    async def health(self, deployment: DeploymentRecord) -> HealthReport:
        return HealthReport(state=HealthState.READY, latency_ms=0, detail="mock")

    async def capabilities(self, deployment: DeploymentRecord) -> ModelCapabilities:
        declared = ModelCapabilities(
            streaming=True,
            embeddings=True,
            reasoning=True,
            coding=True,
            multilingual=True,
        )
        return declared.model_copy(update=deployment.capability_overrides)
