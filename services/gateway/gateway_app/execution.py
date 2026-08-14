"""Execution: run the resolved candidates and produce the response.

The fallback rule is the important one, and it is a correctness rule rather than
a policy: **once the first token has been sent, there is no fallback.** A
half-streamed answer cannot be silently replaced by another model's answer, so a
mid-stream failure surfaces as a stream error with partial usage recorded.

Every candidate tried here already passed policy in the resolver, so fallback can
never widen what the caller is permitted to reach.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from janus_core.errors import JanusError, UnavailableError
from janus_core.logging import get_logger
from janus_schemas.chat import (
    ChatChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    JanusResponseMetadata,
    JanusRoutingEvent,
    JanusUsageEvent,
    Usage,
)
from janus_schemas.embeddings import EmbeddingRequest, EmbeddingResponse
from pydantic import BaseModel

from gateway_app.backends import BackendRegistry, CallContext
from gateway_app.backends.mock import estimate_tokens
from gateway_app.cost import estimate_cost_usd
from gateway_app.health import HealthTracker
from gateway_app.router.resolver import Candidate, ResolutionResult
from gateway_app.telemetry.writer import TelemetryWriter, UsageWrite

logger = get_logger(__name__)


@dataclass(slots=True)
class StreamEvent:
    """One SSE event: ``event`` is ``None`` for plain data chunks."""

    data: BaseModel | str
    event: str | None = None


def _metadata(
    candidate: Candidate,
    ctx: CallContext,
    resolution: ResolutionResult,
    *,
    fallback_used: bool,
    ttft_ms: int | None = None,
) -> JanusResponseMetadata:
    return JanusResponseMetadata(
        request_id=ctx.request_id,
        model=candidate.model.slug,
        deployment=candidate.deployment.key,
        provider=candidate.model.provider,
        privacy=candidate.deployment.privacy_level.value,
        region=candidate.deployment.region,
        mode=ctx.mode,
        fallback_used=fallback_used,
        routing_reason=resolution.routing_reason,
        routing_explanation=resolution.explanation,
        ttft_ms=ttft_ms,
        total_ms=ctx.elapsed_ms,
    )


def _log_decision(
    ctx: CallContext,
    candidate: Candidate,
    resolution: ResolutionResult,
    *,
    attempt: int,
    outcome: str,
    usage: Usage | None = None,
    error_code: str | None = None,
    ttft_ms: int | None = None,
) -> None:
    """The routing decision log (docs/observability.md §4).

    Prompt and completion text is never included — only the decision, the
    outcome, and the numbers needed to explain and bill it.
    """
    logger.info(
        "routing_decision",
        extra={
            "request_id": ctx.request_id,
            "organization_id": ctx.organization_id,
            "selected_model": candidate.model.slug,
            "selected_deployment": candidate.deployment.key,
            "provider": candidate.model.provider,
            "privacy": candidate.deployment.privacy_level.value,
            "region": candidate.deployment.region,
            "mode": ctx.mode.value,
            "classification": ctx.classification.value,
            "routing_reason": resolution.routing_reason,
            "candidate_count": len(resolution.candidates),
            "excluded_counts": _excluded_counts(resolution),
            "attempt": attempt,
            "fallback_used": attempt > 1,
            "outcome": outcome,
            "error_code": error_code,
            "ttft_ms": ttft_ms,
            "total_ms": ctx.elapsed_ms,
            "prompt_tokens": usage.prompt_tokens if usage else None,
            "completion_tokens": usage.completion_tokens if usage else None,
        },
    )


def _excluded_counts(resolution: ResolutionResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _, reason in resolution.excluded:
        counts[reason.value] = counts.get(reason.value, 0) + 1
    return counts


class Executor:
    def __init__(
        self,
        backends: BackendRegistry,
        health: HealthTracker,
        *,
        max_attempts: int = 3,
        telemetry: TelemetryWriter | None = None,
    ) -> None:
        self._backends = backends
        self._health = health
        self._max_attempts = max_attempts
        self._telemetry = telemetry or TelemetryWriter(None)

    def _attempts(self, resolution: ResolutionResult) -> list[Candidate]:
        # A pinned deployment is never substituted: the caller asked for exactly
        # that one, and quietly using another would be a lie about provenance.
        if resolution.pinned:
            return [resolution.primary]
        return list(resolution.candidates[: self._max_attempts])

    async def generate(
        self,
        request: ChatCompletionRequest,
        resolution: ResolutionResult,
        ctx: CallContext,
    ) -> tuple[ChatCompletionResponse, JanusResponseMetadata]:
        last_error: JanusError | None = None

        for attempt, candidate in enumerate(self._attempts(resolution), start=1):
            backend = self._backends.get(candidate.deployment.backend)
            started = time.monotonic()
            try:
                response = await backend.generate(request, candidate.deployment, ctx)
            except JanusError as error:
                self._health.record_failure(candidate.deployment.key, error.code)
                _log_decision(
                    ctx,
                    candidate,
                    resolution,
                    attempt=attempt,
                    outcome="error",
                    error_code=error.code,
                )
                if not error.retryable:
                    raise
                last_error = error
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            self._health.record_success(candidate.deployment.key, latency_ms)
            metadata = _metadata(
                candidate, ctx, resolution, fallback_used=attempt > 1, ttft_ms=latency_ms
            )
            response.janus = metadata
            _log_decision(
                ctx,
                candidate,
                resolution,
                attempt=attempt,
                outcome="success",
                usage=response.usage,
                ttft_ms=latency_ms,
            )
            await self._persist_success(
                ctx,
                candidate,
                resolution,
                response.usage,
                ttft_ms=latency_ms,
                fallback_used=attempt > 1,
            )
            return response, metadata

        raise UnavailableError(
            "Every eligible model failed for this request.",
            details={
                "attempts": len(self._attempts(resolution)),
                "last_error_code": last_error.code if last_error else None,
            },
        ) from last_error

    async def stream(
        self,
        request: ChatCompletionRequest,
        resolution: ResolutionResult,
        ctx: CallContext,
    ) -> AsyncIterator[StreamEvent]:
        last_error: JanusError | None = None

        for attempt, candidate in enumerate(self._attempts(resolution), start=1):
            backend = self._backends.get(candidate.deployment.backend)
            iterator = backend.stream(request, candidate.deployment, ctx)

            first_chunk: ChatChunk | None = None
            try:
                async for chunk in iterator:
                    first_chunk = chunk
                    break
            except JanusError as error:
                # Nothing was sent to the client yet, so switching models is safe.
                self._health.record_failure(candidate.deployment.key, error.code)
                _log_decision(
                    ctx,
                    candidate,
                    resolution,
                    attempt=attempt,
                    outcome="error",
                    error_code=error.code,
                )
                if not error.retryable:
                    raise
                last_error = error
                continue

            ttft_ms = ctx.elapsed_ms
            self._health.record_success(candidate.deployment.key, ttft_ms)

            yield StreamEvent(
                event="janus.routing",
                data=JanusRoutingEvent(
                    request_id=ctx.request_id,
                    model=candidate.model.slug,
                    deployment=candidate.deployment.key,
                    provider=candidate.model.provider,
                    privacy=candidate.deployment.privacy_level.value,
                    fallback_used=attempt > 1,
                    routing_explanation=resolution.explanation,
                ),
            )

            usage: Usage | None = None
            emitted_characters = 0

            if first_chunk is not None:
                usage = first_chunk.usage or usage
                emitted_characters += _chunk_length(first_chunk)
                yield StreamEvent(data=first_chunk)

            async for chunk in iterator:
                usage = chunk.usage or usage
                emitted_characters += _chunk_length(chunk)
                yield StreamEvent(data=chunk)

            if usage is None:
                # The runtime reported no usage (Ollama, some local servers), so
                # this is an estimate and is labeled as one in the log.
                prompt_text = " ".join(
                    message.content
                    for message in request.messages
                    if isinstance(message.content, str)
                )
                usage = Usage(
                    prompt_tokens=estimate_tokens(prompt_text),
                    completion_tokens=estimate_tokens("x" * emitted_characters),
                )
                usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
                estimated = True
            else:
                estimated = False

            yield StreamEvent(
                event="janus.usage",
                data=JanusUsageEvent(
                    request_id=ctx.request_id,
                    usage=usage,
                    ttft_ms=ttft_ms,
                    total_ms=ctx.elapsed_ms,
                ),
            )

            logger.info(
                "usage_recorded",
                extra={
                    "request_id": ctx.request_id,
                    "organization_id": ctx.organization_id,
                    "model": candidate.model.slug,
                    "deployment": candidate.deployment.key,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "usage_estimated": estimated,
                },
            )
            _log_decision(
                ctx,
                candidate,
                resolution,
                attempt=attempt,
                outcome="success",
                usage=usage,
                ttft_ms=ttft_ms,
            )
            await self._persist_success(
                ctx,
                candidate,
                resolution,
                usage,
                ttft_ms=ttft_ms,
                fallback_used=attempt > 1,
                usage_estimated=estimated,
            )
            yield StreamEvent(data="[DONE]")
            return

        raise UnavailableError(
            "Every eligible model failed for this request.",
            details={
                "attempts": len(self._attempts(resolution)),
                "last_error_code": last_error.code if last_error else None,
            },
        ) from last_error

    async def embeddings(
        self,
        request: EmbeddingRequest,
        resolution: ResolutionResult,
        ctx: CallContext,
    ) -> EmbeddingResponse:
        candidate = resolution.primary
        backend = self._backends.get(candidate.deployment.backend)
        response = await backend.embeddings(request, candidate.deployment, ctx)
        if ctx.organization_id:
            cost = estimate_cost_usd(candidate.model, response.usage)
            await self._telemetry.record_usage(
                UsageWrite(
                    request_id=ctx.request_id,
                    organization_id=ctx.organization_id,
                    model=candidate.model,
                    deployment_key=candidate.deployment.key,
                    usage=response.usage,
                    operation="embedding",
                    ttft_ms=ctx.elapsed_ms,
                    total_ms=ctx.elapsed_ms,
                    cost_usd=str(cost),
                )
            )
        return response

    async def _persist_success(
        self,
        ctx: CallContext,
        candidate: Candidate,
        resolution: ResolutionResult,
        usage: Usage,
        *,
        ttft_ms: int | None,
        fallback_used: bool,
        usage_estimated: bool = False,
    ) -> None:
        if not ctx.organization_id:
            return
        cost = estimate_cost_usd(candidate.model, usage)
        await self._telemetry.record_routing_decision(
            request_id=ctx.request_id,
            organization_id=ctx.organization_id,
            resolution=resolution,
            selected=candidate,
            requested_model=resolution.routing_reason,
            mode=ctx.mode.value,
            classification=ctx.classification.value,
            requirements={},
            decision_ms=ctx.elapsed_ms,
            fallback_used=fallback_used,
        )
        await self._telemetry.record_usage(
            UsageWrite(
                request_id=ctx.request_id,
                organization_id=ctx.organization_id,
                model=candidate.model,
                deployment_key=candidate.deployment.key,
                usage=usage,
                ttft_ms=ttft_ms,
                total_ms=ctx.elapsed_ms,
                cost_usd=str(cost),
                fallback_used=fallback_used,
                usage_estimated=usage_estimated,
            )
        )


def _chunk_length(chunk: ChatChunk) -> int:
    total = 0
    for choice in chunk.choices:
        content: Any = choice.delta.content
        if isinstance(content, str):
            total += len(content)
    return total
