"""Inference passthrough.

Phase 1 exposes just enough to prove the path end to end: the browser talks to
the control plane, the control plane resolves policy and calls the gateway, and
the gateway reaches a model. Nothing is persisted yet — conversations and message
history are Phase 2, which is why this router is explicitly a passthrough rather
than a chat API.

The control plane never contacts a provider itself, and it never chooses a model:
it supplies the caller's organization and policy context, and the gateway decides.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse, StreamingResponse
from janus_core.logging import get_logger
from janus_schemas.chat import ChatCompletionRequest

from api_app.deps import (
    ClassificationDep,
    GatewayDep,
    ModeDep,
    PrincipalDep,
    RequestIdDep,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["inference"])


@router.get("/models")
async def list_models(
    principal: PrincipalDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    """The model catalog, filtered by what this organization may actually use."""
    return await gateway.list_models(
        organization_id=principal.organization_id,
        request_id=request_id,
        mode=mode,
        classification=classification,
        actor_id=principal.actor_id,
    )


@router.post("/chat", response_model=None)
async def chat(
    body: ChatCompletionRequest,
    principal: PrincipalDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> Response:
    payload = body.model_dump(exclude_none=True)

    if body.stream:
        stream = gateway.stream_chat_completion(
            payload,
            organization_id=principal.organization_id,
            request_id=request_id,
            mode=mode,
            classification=classification,
            actor_id=principal.actor_id,
        )
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Janus-Request-Id": request_id,
            },
        )

    status_code, completion = await gateway.chat_completion(
        payload,
        organization_id=principal.organization_id,
        request_id=request_id,
        mode=mode,
        classification=classification,
        actor_id=principal.actor_id,
    )
    return JSONResponse(status_code=status_code, content=completion)
