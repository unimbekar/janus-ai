"""Inference passthrough and the public OpenAI-compatible surface.

The control plane never contacts a provider and never chooses a model: it
supplies organization and policy context, and the gateway decides. ``POST /v1/chat``
is the original Phase 1 path; ``POST /v1/chat/completions`` is the same handler,
so an unmodified OpenAI SDK can set ``base_url`` at this origin's ``/v1``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse, StreamingResponse
from janus_core.logging import get_logger
from janus_schemas.chat import ChatCompletionRequest
from janus_schemas.common import Classification, ExecutionMode
from janus_schemas.embeddings import EmbeddingRequest

from api_app.deps import (
    ClassificationDep,
    GatewayDep,
    ModeDep,
    Principal,
    PrincipalDep,
    RequestIdDep,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["inference"])


def _policy(
    principal: Principal,
    mode: ExecutionMode,
    classification: Classification,
    request_id: str,
) -> dict[str, Any]:
    return {
        "organization_id": principal.organization_id,
        "request_id": request_id,
        "mode": mode,
        "classification": classification,
        "actor_id": principal.actor_id,
    }


@router.get("/models")
async def list_models(
    principal: PrincipalDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    """The model catalog, filtered by what this organization may actually use."""
    return await gateway.list_models(**_policy(principal, mode, classification, request_id))


@router.get("/models/{model_id:path}")
async def get_model(
    model_id: str,
    principal: PrincipalDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    return await gateway.get_model(model_id, **_policy(principal, mode, classification, request_id))


@router.get("/providers")
async def list_providers(
    principal: PrincipalDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    return await gateway.list_providers(**_policy(principal, mode, classification, request_id))


@router.post("/chat", response_model=None)
@router.post("/chat/completions", response_model=None)
async def chat(
    body: ChatCompletionRequest,
    principal: PrincipalDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> Response:
    payload = body.model_dump(exclude_none=True)
    context = _policy(principal, mode, classification, request_id)

    if body.stream:
        stream = gateway.stream_chat_completion(payload, **context)
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Janus-Request-Id": request_id,
            },
        )

    status_code, completion = await gateway.chat_completion(payload, **context)
    return JSONResponse(status_code=status_code, content=completion)


@router.post("/embeddings")
async def embeddings(
    body: EmbeddingRequest,
    principal: PrincipalDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> JSONResponse:
    status_code, payload = await gateway.embeddings(
        body.model_dump(exclude_none=True),
        **_policy(principal, mode, classification, request_id),
    )
    return JSONResponse(status_code=status_code, content=payload)
