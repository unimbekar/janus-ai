"""``POST /v1/chat/completions`` — the only way to reach a model."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse, StreamingResponse
from janus_core.errors import JanusError
from janus_core.logging import get_logger
from janus_schemas.chat import ChatCompletionRequest, ChatCompletionResponse
from janus_schemas.common import Classification, ExecutionMode, ModelType

from gateway_app.backends import CallContext
from gateway_app.deps import CallerDep, ExecutorDep, RegistryDep, ResolverDep, SettingsDep
from gateway_app.execution import StreamEvent
from gateway_app.router.infer import infer_requirements, merge_requirements
from gateway_app.router.resolver import ResolutionRequest
from gateway_app.router.scoring import WeightProfile

logger = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["inference"])


def _effective_mode(request: ChatCompletionRequest, caller_mode: ExecutionMode) -> ExecutionMode:
    """Most restrictive of the caller's policy and the request's own ask.

    A request can tighten its execution mode but never loosen the one the control
    plane resolved from organization policy.
    """
    requested = request.janus.mode
    if requested is None:
        return caller_mode
    return max(caller_mode, requested, key=lambda mode: mode.restrictiveness)


def _effective_classification(
    request: ChatCompletionRequest, caller_classification: Classification
) -> Classification:
    """Highest classification present wins."""
    requested = request.janus.classification
    if requested is None:
        return caller_classification
    return max(caller_classification, requested, key=lambda value: value.rank)


def _sse(event: StreamEvent) -> str:
    if isinstance(event.data, str):
        payload = event.data
    else:
        payload = event.data.model_dump_json(exclude_none=True)
    prefix = f"event: {event.event}\n" if event.event else ""
    return f"{prefix}data: {payload}\n\n"


@router.post(
    "/chat/completions",
    # The response is either a JSON completion or an SSE stream, so the shape is
    # chosen at runtime rather than declared once.
    response_model=None,
    responses={200: {"model": ChatCompletionResponse}},
)
async def chat_completions(
    request: ChatCompletionRequest,
    caller: CallerDep,
    registry: RegistryDep,
    resolver: ResolverDep,
    executor: ExecutorDep,
    settings: SettingsDep,
) -> Response:
    mode = _effective_mode(request, caller.mode)
    classification = _effective_classification(request, caller.classification)
    explicit = request.janus.requirements
    inferred = infer_requirements(request.messages)
    preferences = merge_requirements(explicit, inferred)
    try:
        profile = WeightProfile(request.janus.routing.profile)
    except ValueError:
        profile = WeightProfile.BALANCED

    ctx = CallContext(
        request_id=caller.request_id,
        organization_id=caller.organization_id,
        classification=classification,
        mode=mode,
        timeout_seconds=settings.request_timeout_seconds,
        first_token_timeout_seconds=settings.first_token_timeout_seconds,
    )

    resolution = resolver.resolve(
        registry.current,
        ResolutionRequest(
            model=request.model,
            mode=mode,
            classification=classification,
            requirements=explicit,
            preferences=preferences,
            constraints=request.janus.constraints,
            profile=profile,
            model_type=ModelType.CHAT,
        ),
    )

    if not request.stream:
        completion, _ = await executor.generate(request, resolution, ctx)
        if not request.janus.routing.explain and completion.janus is not None:
            completion.janus.routing_explanation = None
        return JSONResponse(content=completion.model_dump(mode="json"))

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in executor.stream(request, resolution, ctx):
                yield _sse(event)
        except JanusError as error:
            # The HTTP status is already 200 at this point, so the failure has to
            # travel as a terminal event the client can distinguish.
            logger.warning(
                "stream_failed_after_start",
                extra={"request_id": ctx.request_id, "error_code": error.code},
            )
            yield f"event: janus.error\ndata: {_error_json(error, ctx.request_id)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Janus-Request-Id": ctx.request_id,
        },
    )


def _error_json(error: JanusError, request_id: str) -> str:
    return json.dumps(error.to_payload(request_id))
