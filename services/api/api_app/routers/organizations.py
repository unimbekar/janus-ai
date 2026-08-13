"""Organizations, members, and API keys."""

from __future__ import annotations

from fastapi import APIRouter, status
from janus_core.errors import NotFoundError
from sqlalchemy import select

from api_app.deps import DatabaseDep, IdentityDep, PrincipalDep, SessionDep
from api_app.models import ApiKey, Organization, OrganizationMember, User
from api_app.schemas import (
    AddMemberRequest,
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreatedApiKeyResponse,
    CreateOrganizationRequest,
    MemberResponse,
    OrganizationResponse,
    UpdateOrganizationRequest,
)

router = APIRouter(prefix="/v1/organizations", tags=["organizations"])


def _response(organization: Organization, role: str | None = None) -> OrganizationResponse:
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


@router.get("", response_model=list[OrganizationResponse])
async def list_organizations(
    principal: PrincipalDep, session: SessionDep, identity: IdentityDep
) -> list[OrganizationResponse]:
    if principal.user_id is None:
        return []
    memberships = await identity.organizations_for(session, principal.user_id)
    return [_response(organization, role) for organization, role in memberships]


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: CreateOrganizationRequest,
    principal: PrincipalDep,
    db: DatabaseDep,
    identity: IdentityDep,
) -> OrganizationResponse:
    principal.require_role("member")
    assert principal.user_id is not None

    # Creating a tenant is cross-tenant work, so it runs outside the caller's
    # organization context.
    async with db.session(user_id=principal.user_id) as session:
        user = await session.get(User, principal.user_id)
        if user is None:
            raise NotFoundError("User not found.", code="user_not_found")
        organization = await identity.create_organization(session, name=body.name, owner=user)
        await identity.record_audit(
            session,
            organization_id=organization.id,
            actor_type=principal.actor_type,
            actor_id=principal.actor_id,
            action="organization.created",
            resource_type="organization",
            resource_id=organization.id,
        )
        return _response(organization, "owner")


@router.get("/current", response_model=OrganizationResponse)
async def get_current_organization(
    principal: PrincipalDep, session: SessionDep
) -> OrganizationResponse:
    organization = await session.get(Organization, principal.organization_id)
    if organization is None:
        raise NotFoundError("Organization not found.", code="organization_not_found")
    return _response(organization, principal.role)


@router.patch("/current", response_model=OrganizationResponse)
async def update_current_organization(
    body: UpdateOrganizationRequest,
    principal: PrincipalDep,
    session: SessionDep,
    identity: IdentityDep,
) -> OrganizationResponse:
    principal.require_role("admin")

    organization = await session.get(Organization, principal.organization_id)
    if organization is None:
        raise NotFoundError("Organization not found.", code="organization_not_found")

    changed: dict[str, str] = {}
    if body.name is not None:
        organization.name = body.name.strip()
        changed["name"] = organization.name
    if body.default_mode is not None:
        organization.default_mode = body.default_mode
        changed["default_mode"] = body.default_mode.value
    if body.default_classification is not None:
        organization.default_classification = body.default_classification
        changed["default_classification"] = body.default_classification.value

    if changed:
        # Policy-relevant settings are audited: who changed the execution mode,
        # and when, is the first question after an incident.
        await identity.record_audit(
            session,
            organization_id=organization.id,
            actor_type=principal.actor_type,
            actor_id=principal.actor_id,
            action="organization.updated",
            resource_type="organization",
            resource_id=organization.id,
            metadata={"changed": changed},
        )

    return _response(organization, principal.role)


@router.get("/current/members", response_model=list[MemberResponse])
async def list_members(principal: PrincipalDep, session: SessionDep) -> list[MemberResponse]:
    rows = await session.execute(
        select(OrganizationMember, User)
        .join(User, User.id == OrganizationMember.user_id)
        .where(OrganizationMember.organization_id == principal.organization_id)
        .order_by(OrganizationMember.joined_at)
    )
    return [
        MemberResponse(
            user_id=user.id,
            email=user.email,
            name=user.name,
            role=member.role,
            joined_at=member.joined_at,
        )
        for member, user in rows.all()
    ]


@router.post("/current/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def add_member(
    body: AddMemberRequest,
    principal: PrincipalDep,
    db: DatabaseDep,
    session: SessionDep,
    identity: IdentityDep,
) -> MemberResponse:
    principal.require_role("admin")
    assert principal.user_id is not None

    # Looking up the invitee is cross-tenant by nature: they are not a member yet.
    async with db.session() as lookup:
        user = await lookup.scalar(select(User).where(User.email == body.email.strip().lower()))

    member = await identity.add_member(
        session,
        organization_id=principal.organization_id,
        email=body.email,
        role=body.role,
        invited_by=principal.user_id,
    )
    await identity.record_audit(
        session,
        organization_id=principal.organization_id,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        action="member.added",
        resource_type="user",
        resource_id=member.user_id,
        metadata={"role": body.role},
    )

    assert user is not None  # add_member raises if the account does not exist
    return MemberResponse(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=member.role,
        joined_at=member.joined_at,
    )


@router.get("/current/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(principal: PrincipalDep, session: SessionDep) -> list[ApiKeyResponse]:
    principal.require_role("admin")

    keys = (
        await session.scalars(
            select(ApiKey)
            .where(ApiKey.organization_id == principal.organization_id)
            .order_by(ApiKey.created_at.desc())
        )
    ).all()
    return [
        ApiKeyResponse(
            id=key.id,
            name=key.name,
            prefix=key.prefix,
            scopes=list(key.scopes),
            mode_ceiling=key.mode_ceiling,
            last_used_at=key.last_used_at,
            revoked_at=key.revoked_at,
            created_at=key.created_at,
        )
        for key in keys
    ]


@router.post(
    "/current/api-keys",
    response_model=CreatedApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    body: CreateApiKeyRequest,
    principal: PrincipalDep,
    session: SessionDep,
    identity: IdentityDep,
) -> CreatedApiKeyResponse:
    principal.require_role("admin")
    assert principal.user_id is not None

    record, key = await identity.create_api_key(
        session,
        organization_id=principal.organization_id,
        created_by=principal.user_id,
        name=body.name,
        scopes=body.scopes,
        mode_ceiling=body.mode_ceiling,
    )
    await identity.record_audit(
        session,
        organization_id=principal.organization_id,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        action="api_key.created",
        resource_type="api_key",
        resource_id=record.id,
        metadata={"scopes": body.scopes, "prefix": record.prefix},
    )

    # The only time the plaintext key exists outside the caller's request.
    return CreatedApiKeyResponse(
        id=record.id,
        name=record.name,
        prefix=record.prefix,
        scopes=list(record.scopes),
        mode_ceiling=record.mode_ceiling,
        created_at=record.created_at,
        key=key,
    )


@router.delete("/current/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    identity: IdentityDep,
) -> None:
    principal.require_role("admin")

    await identity.revoke_api_key(session, organization_id=principal.organization_id, key_id=key_id)
    await identity.record_audit(
        session,
        organization_id=principal.organization_id,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        action="api_key.revoked",
        resource_type="api_key",
        resource_id=key_id,
    )
