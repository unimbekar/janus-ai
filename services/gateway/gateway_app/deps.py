"""Dependencies: caller authentication and access to service singletons."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from janus_core.errors import AuthenticationError, RateLimitError, ValidationError
from janus_core.ids import IdPrefix, new_id
from janus_core.logging import get_logger
from janus_schemas.common import Classification, ExecutionMode

from gateway_app.execution import Executor
from gateway_app.health import HealthTracker
from gateway_app.rate_limit import RateLimiter
from gateway_app.registry.service import RegistryService
from gateway_app.router.resolver import ModelResolver
from gateway_app.settings import GatewaySettings

logger = get_logger(__name__)

API_KEY_PREFIX = "jsk"


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is asking, and under what policy the control plane resolved."""

    service: str
    organization_id: str
    request_id: str
    actor_id: str | None = None
    user_id: str | None = None
    api_key_id: str | None = None
    mode: ExecutionMode = ExecutionMode.AUTO
    classification: Classification = Classification.INTERNAL
    auth_kind: str = "service"


def get_settings_for_app(request: Request) -> GatewaySettings:
    return request.app.state.settings


def get_registry_service(request: Request) -> RegistryService:
    return request.app.state.registry_service


def get_health_tracker(request: Request) -> HealthTracker:
    return request.app.state.health


def get_resolver(request: Request) -> ModelResolver:
    return request.app.state.resolver


def get_executor(request: Request) -> Executor:
    return request.app.state.executor


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


async def require_caller(
    request: Request,
    settings: Annotated[GatewaySettings, Depends(get_settings_for_app)],
    authorization: Annotated[str | None, Header()] = None,
    x_janus_organization_id: Annotated[str | None, Header()] = None,
    x_janus_actor_id: Annotated[str | None, Header()] = None,
    x_janus_service: Annotated[str | None, Header()] = None,
    x_janus_mode: Annotated[str | None, Header()] = None,
    x_janus_classification: Annotated[str | None, Header()] = None,
) -> Caller:
    request_id = getattr(request.state, "request_id", None) or new_id(IdPrefix.REQUEST)
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()

    # Public API key path (Phase 3)
    if presented.startswith(f"{API_KEY_PREFIX}_"):
        if not settings.public_api_enabled:
            raise AuthenticationError("Public API access is disabled.", code="public_api_disabled")
        auth = getattr(request.app.state, "api_key_auth", None)
        if auth is None:
            raise AuthenticationError(
                "API key authentication requires database configuration.",
                code="gateway_misconfigured",
            )
        identity = await auth.authenticate(presented)
        limiter: RateLimiter = request.app.state.rate_limiter
        key = f"org:{identity.organization_id}"
        if not await limiter.allow_async(key):
            raise RateLimitError(
                "Rate limit exceeded for this organization.",
                code="rate_limit_exceeded",
            )
        mode = ExecutionMode(x_janus_mode) if x_janus_mode else ExecutionMode.AUTO
        if identity.mode_ceiling:
            ceiling = ExecutionMode(identity.mode_ceiling)
            mode = max(mode, ceiling, key=lambda value: value.restrictiveness)
        classification = (
            Classification(x_janus_classification)
            if x_janus_classification
            else Classification.INTERNAL
        )
        return Caller(
            service="public_api",
            organization_id=identity.organization_id,
            request_id=request_id,
            api_key_id=identity.id,
            mode=mode,
            classification=classification,
            auth_kind="api_key",
        )

    # Internal service token path (control plane, workers)
    expected = settings.gateway_service_token
    if not expected:
        if not settings.is_local:
            raise AuthenticationError(
                "Gateway service token is not configured.",
                code="gateway_misconfigured",
            )
        logger.warning("gateway_auth_disabled", extra={"environment": settings.environment.value})
    elif not presented or not secrets.compare_digest(presented, expected):
        raise AuthenticationError("Invalid gateway credentials.")

    if not x_janus_organization_id:
        raise ValidationError(
            "Organization context is required.",
            code="missing_organization_context",
            param="X-Janus-Organization-Id",
        )

    try:
        mode = ExecutionMode(x_janus_mode) if x_janus_mode else ExecutionMode.AUTO
        classification = (
            Classification(x_janus_classification)
            if x_janus_classification
            else Classification.INTERNAL
        )
    except ValueError as exc:
        raise ValidationError(f"Invalid policy header: {exc}") from exc

    return Caller(
        service=x_janus_service or "unknown",
        organization_id=x_janus_organization_id,
        request_id=request_id,
        actor_id=x_janus_actor_id,
        mode=mode,
        classification=classification,
        auth_kind="service",
    )


CallerDep = Annotated[Caller, Depends(require_caller)]
RegistryDep = Annotated[RegistryService, Depends(get_registry_service)]
HealthDep = Annotated[HealthTracker, Depends(get_health_tracker)]
ResolverDep = Annotated[ModelResolver, Depends(get_resolver)]
ExecutorDep = Annotated[Executor, Depends(get_executor)]
SettingsDep = Annotated[GatewaySettings, Depends(get_settings_for_app)]
