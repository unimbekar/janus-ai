"""Request-scoped dependencies: authentication, tenant context, and services.

Authentication produces a ``Principal``, which is the only thing routers use.
Whether the caller arrived with a browser session or an API key is settled here
once, so no route has to care.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from janus_core.errors import AuthenticationError, AuthorizationError
from janus_schemas.common import Classification, ExecutionMode
from sqlalchemy.ext.asyncio import AsyncSession

from api_app.db import Database
from api_app.gateway_client import GatewayClient
from api_app.identity import IdentityService, role_at_least
from api_app.models import Organization
from api_app.settings import ApiSettings


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making this request, and the ceiling they operate under."""

    kind: str  # "user" | "api_key"
    organization_id: str
    user_id: str | None = None
    api_key_id: str | None = None
    role: str = "member"
    scopes: tuple[str, ...] = ()
    mode_ceiling: ExecutionMode | None = None
    session_token: str | None = None

    @property
    def actor_id(self) -> str:
        return self.user_id or self.api_key_id or "unknown"

    @property
    def actor_type(self) -> str:
        return self.kind

    def require_role(self, minimum: str) -> None:
        if self.kind == "api_key":
            # API keys carry scopes, not roles; role-gated administration is a
            # session-authenticated action in Phase 1.
            raise AuthorizationError(
                "This action requires a signed-in user.",
                code="session_required",
            )
        if not role_at_least(self.role, minimum):
            raise AuthorizationError(
                "Your role does not permit this action.",
                code="insufficient_role",
                details={"required_role": minimum, "your_role": self.role},
            )


def get_settings_for_app(request: Request) -> ApiSettings:
    """The settings this app was built with.

    Not ``get_settings()``: a test or an embedding process may construct an app
    with explicit settings, and a dependency that re-read the environment would
    quietly disagree with the app it serves.
    """
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_identity(request: Request) -> IdentityService:
    return request.app.state.identity


def get_gateway(request: Request) -> GatewayClient:
    return request.app.state.gateway


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "rq_unknown")


async def require_principal(
    request: Request,
    db: Annotated[Database, Depends(get_db)],
    identity: Annotated[IdentityService, Depends(get_identity)],
    settings: Annotated[ApiSettings, Depends(get_settings_for_app)],
) -> Principal:
    authorization = request.headers.get("Authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return await _principal_from_api_key(authorization[7:].strip(), db, identity)

    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie:
        return await _principal_from_session(cookie, db, identity)

    raise AuthenticationError("Authentication is required.", code="missing_credentials")


async def _principal_from_api_key(key: str, db: Database, identity: IdentityService) -> Principal:
    # Authentication happens without tenant context; the key's organization is
    # the result of authentication, never an input to it.
    async with db.session() as session:
        api_key = await identity.resolve_api_key(session, key)

    async with db.session(organization_id=api_key.organization_id) as session:
        await identity.touch_api_key(session, api_key.id)

    return Principal(
        kind="api_key",
        organization_id=api_key.organization_id,
        api_key_id=api_key.id,
        scopes=tuple(api_key.scopes),
        mode_ceiling=api_key.mode_ceiling,
    )


async def _principal_from_session(token: str, db: Database, identity: IdentityService) -> Principal:
    async with db.session() as session:
        record, user = await identity.resolve_session(session, token)
        organization_id = record.organization_id

    if organization_id is None:
        raise AuthenticationError(
            "This session has no active organization.", code="organization_required"
        )

    async with db.session(organization_id=organization_id, user_id=user.id) as session:
        role = await identity.require_membership(
            session, organization_id=organization_id, user_id=user.id
        )

    return Principal(
        kind="user",
        organization_id=organization_id,
        user_id=user.id,
        role=role,
        session_token=token,
    )


async def tenant_session(
    principal: Annotated[Principal, Depends(require_principal)],
    db: Annotated[Database, Depends(get_db)],
) -> AsyncIterator[AsyncSession]:
    """A transaction scoped to the caller's organization."""
    async with db.session(
        organization_id=principal.organization_id, user_id=principal.user_id
    ) as session:
        yield session


async def effective_mode(
    principal: Annotated[Principal, Depends(require_principal)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> ExecutionMode:
    """The organization's default mode, narrowed by any API key ceiling.

    Full policy resolution — platform, organization, team, agent, key — lands in
    Phase 9. The narrowing rule is implemented now because getting it wrong later
    would silently widen what a key can reach.
    """
    organization = await session.get(Organization, principal.organization_id)
    mode = organization.default_mode if organization else ExecutionMode.AUTO

    if principal.mode_ceiling is not None:
        mode = max(mode, principal.mode_ceiling, key=lambda value: value.restrictiveness)
    return mode


async def default_classification(
    principal: Annotated[Principal, Depends(require_principal)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> Classification:
    organization = await session.get(Organization, principal.organization_id)
    return organization.default_classification if organization else Classification.INTERNAL


PrincipalDep = Annotated[Principal, Depends(require_principal)]
SessionDep = Annotated[AsyncSession, Depends(tenant_session)]
DatabaseDep = Annotated[Database, Depends(get_db)]
IdentityDep = Annotated[IdentityService, Depends(get_identity)]
GatewayDep = Annotated[GatewayClient, Depends(get_gateway)]
SettingsDep = Annotated[ApiSettings, Depends(get_settings_for_app)]
RequestIdDep = Annotated[str, Depends(get_request_id)]
ModeDep = Annotated[ExecutionMode, Depends(effective_mode)]
ClassificationDep = Annotated[Classification, Depends(default_classification)]
