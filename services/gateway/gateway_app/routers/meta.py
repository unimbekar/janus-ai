"""Operational endpoints: liveness, readiness, and deployment health."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from janus_schemas.common import HealthState

from gateway_app import __version__
from gateway_app.deps import CallerDep, HealthDep, RegistryDep, SettingsDep

router = APIRouter(tags=["operations"])


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    """Is the process alive? Never touches dependencies."""
    return {"status": "ok", "service": "janus-gateway", "version": __version__}


@router.get("/readyz")
async def readiness(response: Response, registry: RegistryDep, health: HealthDep) -> dict[str, Any]:
    """Can this instance serve? A catalog with no routable deployment cannot."""
    models = registry.current.servable_models()
    routable = [
        deployment.key
        for model in models
        for deployment in model.deployments
        if health.state_for(deployment.key).is_routable
    ]
    if not routable:
        # Every request would fail, so this instance should leave the pool.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if routable else "degraded",
        "environment": registry.current.environment,
        "model_count": len(models),
        "routable_deployments": len(routable),
    }


@router.get("/internal/deployments")
async def deployment_health(
    caller: CallerDep,
    registry: RegistryDep,
    health: HealthDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Operator view of deployment health.

    Endpoints and credential references are omitted deliberately — this is a
    debugging aid, not a way to read infrastructure configuration.
    """
    snapshot = health.snapshot()
    return {
        "environment": settings.environment.value,
        "deployments": [
            {
                "key": deployment.key,
                "model": model.slug,
                "backend": deployment.backend,
                "type": deployment.deployment_type.value,
                "privacy": deployment.privacy_level.value,
                "region": deployment.region,
                "state": snapshot[deployment.key].state.value
                if deployment.key in snapshot
                else HealthState.READY.value,
                "consecutive_failures": snapshot[deployment.key].consecutive_failures
                if deployment.key in snapshot
                else 0,
                "last_latency_ms": snapshot[deployment.key].last_latency_ms
                if deployment.key in snapshot
                else None,
                "last_error": snapshot[deployment.key].last_error
                if deployment.key in snapshot
                else None,
            }
            for model in registry.current.models
            for deployment in model.deployments
        ],
    }
