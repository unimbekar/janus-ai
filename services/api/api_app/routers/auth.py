"""Authentication: register, sign in, sign out, session introspection."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from janus_core.errors import AuthenticationError, NotFoundError
from janus_core.logging import get_logger

from api_app.db import set_scope
from api_app.deps import (
    DatabaseDep,
    IdentityDep,
    PrincipalDep,
    SettingsDep,
    get_settings_for_app,
)
from api_app.models import Organization, User
from api_app.schemas import (
    LoginRequest,
    OrganizationResponse,
    RegisterRequest,
    SessionResponse,
    SwitchOrganizationRequest,
    UserResponse,
)
from api_app.settings import ApiSettings

logger = get_logger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["authentication"])


def _client_ip(request: Request) -> str | None:
    # Behind an ALB, the left-most XFF entry is the client. Trusting the header
    # is only safe because nothing is reachable except through the load balancer.
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _set_session_cookie(response: Response, token: str, settings: ApiSettings) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        domain=settings.session_cookie_domain,
        path="/",
    )


def _organization_response(
    organization: Organization, role: str | None = None
) -> OrganizationResponse:
    return OrganizationResponse(
        id=organization.id,
        slug=organization.slug,
        name=organization.name,
        plan=organization.plan,
        default_mode=organization.default_mode,
        default_classification=organization.default_classification,
        role=role,
        created_at=organization.created_at,
    )


@router.post("/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    db: DatabaseDep,
    identity: IdentityDep,
    settings: SettingsDep,
) -> SessionResponse:
    async with db.session() as session:
        user, organization = await identity.register(
            session,
            email=body.email,
            password=body.password,
            name=body.name,
            organization_name=body.organization_name,
        )
        _, token = await identity.start_session(
            session,
            user=user,
            organization_id=organization.id,
            ip=_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
        )
        payload = SessionResponse(
            user=UserResponse(
                id=user.id, email=user.email, name=user.name, email_verified=user.email_verified
            ),
            organization=_organization_response(organization, "owner"),
            organizations=[_organization_response(organization, "owner")],
        )

    _set_session_cookie(response, token, settings)
    logger.info("user_registered", extra={"user_id": payload.user.id})
    return payload


@router.post("/login", response_model=SessionResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DatabaseDep,
    identity: IdentityDep,
    settings: SettingsDep,
) -> SessionResponse:
    async with db.session() as session:
        user = await identity.authenticate(session, email=body.email, password=body.password)

    async with db.session(user_id=user.id) as session:
        memberships = await identity.organizations_for(session, user.id)
        if not memberships:
            raise AuthenticationError(
                "This account has no organization.", code="organization_required"
            )
        organization, role = memberships[0]

        # The active organization is only known once memberships are read, so the
        # tenant scope is set here — before writing the audit event, which is
        # tenant-scoped.
        await set_scope(session, organization_id=organization.id)

        payload = SessionResponse(
            user=UserResponse(
                id=user.id, email=user.email, name=user.name, email_verified=user.email_verified
            ),
            organization=_organization_response(organization, role),
            organizations=[
                _organization_response(item, item_role) for item, item_role in memberships
            ],
        )
        _, token = await identity.start_session(
            session,
            user=user,
            organization_id=organization.id,
            ip=_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
        )
        await identity.record_audit(
            session,
            organization_id=organization.id,
            actor_type="user",
            actor_id=user.id,
            action="user.signed_in",
            resource_type="session",
            ip=_client_ip(request),
        )

    _set_session_cookie(response, token, settings)
    return payload


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: DatabaseDep,
    identity: IdentityDep,
    settings: SettingsDep,
) -> Response:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        async with db.session() as session:
            await identity.revoke_session(session, token)

    response.delete_cookie(
        settings.session_cookie_name,
        domain=settings.session_cookie_domain,
        path="/",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/session", response_model=SessionResponse)
async def current_session(
    principal: PrincipalDep,
    db: DatabaseDep,
    identity: IdentityDep,
) -> SessionResponse:
    if principal.user_id is None:
        raise AuthenticationError(
            "This endpoint requires a signed-in user.", code="session_required"
        )

    async with db.session(
        organization_id=principal.organization_id, user_id=principal.user_id
    ) as session:
        user = await session.get(User, principal.user_id)
        organization = await session.get(Organization, principal.organization_id)
        if user is None or organization is None:
            raise NotFoundError("Session is no longer valid.", code="session_invalid")
        memberships = await identity.organizations_for(session, principal.user_id)

        return SessionResponse(
            user=UserResponse(
                id=user.id, email=user.email, name=user.name, email_verified=user.email_verified
            ),
            organization=_organization_response(organization, principal.role),
            organizations=[
                _organization_response(item, item_role) for item, item_role in memberships
            ],
        )


@router.post("/switch-organization", response_model=SessionResponse)
async def switch_organization(
    body: SwitchOrganizationRequest,
    principal: PrincipalDep,
    db: DatabaseDep,
    identity: IdentityDep,
    settings: Annotated[ApiSettings, Depends(get_settings_for_app)],
) -> SessionResponse:
    if principal.session_token is None or principal.user_id is None:
        raise AuthenticationError(
            "Switching organizations requires a signed-in user.", code="session_required"
        )

    async with db.session(user_id=principal.user_id) as session:
        record, user = await identity.resolve_session(session, principal.session_token)
        await identity.switch_organization(
            session, session_record=record, organization_id=body.organization_id
        )
        memberships = await identity.organizations_for(session, user.id)
        organization, role = next(
            (item, item_role) for item, item_role in memberships if item.id == body.organization_id
        )
        return SessionResponse(
            user=UserResponse(
                id=user.id, email=user.email, name=user.name, email_verified=user.email_verified
            ),
            organization=_organization_response(organization, role),
            organizations=[
                _organization_response(item, item_role) for item, item_role in memberships
            ],
        )
