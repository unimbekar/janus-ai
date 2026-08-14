"""POST /v1/embeddings — vector generation through the gateway."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from janus_schemas.embeddings import EmbeddingRequest, EmbeddingResponse

from gateway_app.backends import CallContext
from gateway_app.deps import CallerDep, ExecutorDep, RegistryDep, ResolverDep, SettingsDep
from gateway_app.router.resolver import ResolutionRequest

router = APIRouter(prefix="/v1", tags=["inference"])


@router.post("/embeddings", response_model=EmbeddingResponse)
async def embeddings(
    request: EmbeddingRequest,
    caller: CallerDep,
    registry: RegistryDep,
    resolver: ResolverDep,
    executor: ExecutorDep,
    settings: SettingsDep,
) -> JSONResponse:
    ctx = CallContext(
        request_id=caller.request_id,
        organization_id=caller.organization_id,
        classification=caller.classification,
        mode=caller.mode,
        timeout_seconds=settings.request_timeout_seconds,
    )
    resolution = resolver.resolve(
        registry.current,
        ResolutionRequest(
            model=request.model,
            mode=caller.mode,
            classification=caller.classification,
            requirements=request.janus.requirements,
        ),
    )
    response = await executor.embeddings(request, resolution, ctx)
    return JSONResponse(content=response.model_dump(mode="json"))
