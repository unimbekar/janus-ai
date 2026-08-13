"""Dependencies: caller authentication and access to service singletons.

The gateway is an internal service in Phase 1. Its callers are the control plane
and workers, authenticated with a service token, and they pass the organization
context they already authenticated. Public per-organization API keys terminate
here in Phase 3, when the OpenAI-compatible endpoints become externally
reachable.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from janus_core.errors import AuthenticationError, ValidationError
from janus_core.ids import IdPrefix, new_id
from janus_core.logging import get_logger
from janus_schemas.common import Classification, ExecutionMode

from gateway_app.execution import Executor
from gateway_app.health import HealthTracker
from gateway_app.registry.service import RegistryService
from gateway_app.router.resolver import ModelResolver
from gateway_app.settings import GatewaySettings

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is asking, and under what policy the control plane resolved."""

    service: str
    organization_id: str
    request_id: str
    actor_id: str | None = None
    mode: ExecutionMode = ExecutionMode.AUTO
    classification: Classification = Classification.INTERNAL


def get_settings_for_app(request: Request) -> GatewaySettings:
    """The settings this app was built with, rather than a fresh read of the
    environment — so a test that constructs an app with explicit settings and the
    dependency graph cannot disagree."""
    return request.app.state.settings


def get_registry_service(request: Request) -> RegistryService:
    return request.app.state.registry_service


def get_health_tracker(request: Request) -> HealthTracker:
    return request.app.state.health


def get_resolver(request: Request) -> ModelResolver:
    return request.app.state.resolver


def get_executor(request: Request) -> Executor:
    return request.app.state.executor


def require_caller(
    request: Request,
    settings: Annotated[GatewaySettings, Depends(get_settings_for_app)],
    authorization: Annotated[str | None, Header()] = None,
    x_janus_organization_id: Annotated[str | None, Header()] = None,
    x_janus_actor_id: Annotated[str | None, Header()] = None,
    x_janus_service: Annotated[str | None, Header()] = None,
    x_janus_mode: Annotated[str | None, Header()] = None,
    x_janus_classification: Annotated[str | None, Header()] = None,
) -> Caller:
    expected = settings.gateway_service_token

    if not expected:
        # Refusing to start unauthenticated in a deployed environment is more
        # useful than a warning nobody reads.
        if not settings.is_local:
            raise AuthenticationError(
                "Gateway service token is not configured.",
                code="gateway_misconfigured",
            )
        logger.warning("gateway_auth_disabled", extra={"environment": settings.environment.value})
    else:
        presented = ""
        if authorization and authorization.lower().startswith("bearer "):
            presented = authorization[7:]
        if not presented or not secrets.compare_digest(presented, expected):
            raise AuthenticationError("Invalid gateway service credentials.")

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
        request_id=getattr(request.state, "request_id", None) or new_id(IdPrefix.REQUEST),
        actor_id=x_janus_actor_id,
        mode=mode,
        classification=classification,
    )


CallerDep = Annotated[Caller, Depends(require_caller)]
RegistryDep = Annotated[RegistryService, Depends(get_registry_service)]
HealthDep = Annotated[HealthTracker, Depends(get_health_tracker)]
ResolverDep = Annotated[ModelResolver, Depends(get_resolver)]
ExecutorDep = Annotated[Executor, Depends(get_executor)]
SettingsDep = Annotated[GatewaySettings, Depends(get_settings_for_app)]
