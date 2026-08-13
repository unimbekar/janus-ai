"""Model catalog endpoints: ``/v1/models`` and ``/v1/providers``.

Both are filtered by the caller's policy using the same eligibility rules the
router uses, so the catalog never advertises a model the caller cannot use.
"""

from __future__ import annotations

from fastapi import APIRouter
from janus_core.errors import NotFoundError
from janus_schemas.models import ModelInfo, ModelList, ProviderInfo, ProviderList

from gateway_app.deps import CallerDep, HealthDep, RegistryDep, ResolverDep
from gateway_app.router.resolver import ResolutionRequest

router = APIRouter(prefix="/v1", tags=["catalog"])


def _visible(
    registry: RegistryDep, resolver: ResolverDep, caller: CallerDep
) -> dict[str, set[str]]:
    """Slug → eligible deployment keys for this caller."""
    candidates = resolver.eligible_candidates(
        registry.current,
        ResolutionRequest(
            model="auto",
            mode=caller.mode,
            classification=caller.classification,
        ),
    )
    visible: dict[str, set[str]] = {}
    for candidate in candidates:
        visible.setdefault(candidate.model.slug, set()).add(candidate.deployment.key)
    return visible


@router.get("/models", response_model=ModelList)
async def list_models(
    caller: CallerDep,
    registry: RegistryDep,
    resolver: ResolverDep,
    health: HealthDep,
) -> ModelList:
    visible = _visible(registry, resolver, caller)
    availability = health.availability_map()

    entries: list[ModelInfo] = []
    for model in registry.current.models:
        eligible_keys = visible.get(model.slug)
        if not eligible_keys:
            continue
        info = model.to_public(availability)
        info.janus.deployments = [
            deployment for deployment in info.janus.deployments if deployment.key in eligible_keys
        ]
        entries.append(info)

    return ModelList(data=entries)


@router.get("/models/{model_id:path}", response_model=ModelInfo)
async def get_model(
    model_id: str,
    caller: CallerDep,
    registry: RegistryDep,
    resolver: ResolverDep,
    health: HealthDep,
) -> ModelInfo:
    visible = _visible(registry, resolver, caller)
    model = registry.find(model_id)

    # A model that exists but is not permitted is reported as not found: telling
    # a caller which models they are forbidden from using leaks policy.
    if model is None or model.slug not in visible:
        raise NotFoundError(
            "The requested model is not available.",
            code="model_not_found",
            details={"requested": model_id},
        )

    info = model.to_public(health.availability_map())
    eligible_keys = visible[model.slug]
    info.janus.deployments = [
        deployment for deployment in info.janus.deployments if deployment.key in eligible_keys
    ]
    return info


@router.get("/providers", response_model=ProviderList)
async def list_providers(
    caller: CallerDep,
    registry: RegistryDep,
    resolver: ResolverDep,
) -> ProviderList:
    visible = _visible(registry, resolver, caller)

    counts: dict[str, int] = {}
    kinds: dict[str, str] = {}
    for model in registry.current.models:
        if model.slug not in visible:
            continue
        counts[model.provider] = counts.get(model.provider, 0) + 1
        for deployment in model.deployments:
            if deployment.key not in visible[model.slug]:
                continue
            kind = (
                "local"
                if deployment.privacy_level.value == "local"
                else "janus_hosted"
                if deployment.privacy_level.value == "private"
                else "cloud"
            )
            # Most permissive description of how this provider is reachable.
            kinds[model.provider] = kinds.get(model.provider, kind)

    return ProviderList(
        data=[
            ProviderInfo(
                id=provider,
                display_name=provider.replace("_", " ").title(),
                kind=kinds.get(provider, "cloud"),
                model_count=count,
            )
            for provider, count in sorted(counts.items())
        ]
    )
