"""Identity: accounts, sessions, organizations, memberships, and API keys.

Deliberate behaviors:
  - Sign-in failures are indistinguishable between "no such user" and "wrong
    password", including in timing, so the endpoint is not a user-enumeration
    oracle.
  - A new account always lands in an organization. There is no such thing as a
    user without a tenant, which keeps every downstream query tenant-scoped.
  - The first member of an organization is its owner; roles are checked against a
    documented ordering rather than string comparisons scattered around routers.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from janus_core.errors import AuthenticationError, ConflictError, NotFoundError, ValidationError
from janus_core.ids import IdPrefix, new_id
from janus_core.logging import get_logger
from janus_schemas.common import ExecutionMode
from sqlalchemy import CursorResult, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_app.db import set_scope
from api_app.models import ApiKey, AuditEvent, Organization, OrganizationMember, Session, User
from api_app.security import (
    PasswordHashing,
    api_key_lookup_hash,
    hash_session_token,
    new_api_key,
    new_session_token,
)

logger = get_logger(__name__)

ROLE_ORDER = {"viewer": 0, "billing": 1, "member": 2, "admin": 3, "owner": 4}
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LENGTH = 40

#: Argon2id hash of a value nobody knows, verified against when a credential is
#: not found so that "unknown" and "wrong" cost the same.
_DUMMY_ARGON2_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHRzb21lc2FsdA$8Xh0YmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg"
)


@dataclass(frozen=True, slots=True)
class ApiKeyIdentity:
    """Authentication result for an API key, before tenant context is opened."""

    id: str
    organization_id: str
    scopes: list[str] = field(default_factory=list)
    mode_ceiling: ExecutionMode | None = None


def slugify(value: str) -> str:
    slug = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")[:_MAX_SLUG_LENGTH]
    return slug or "org"


def role_at_least(role: str, required: str) -> bool:
    return ROLE_ORDER.get(role, -1) >= ROLE_ORDER.get(required, 99)


class IdentityService:
    def __init__(self, passwords: PasswordHashing, session_ttl_hours: int) -> None:
        self._passwords = passwords
        self._session_ttl = timedelta(hours=session_ttl_hours)

    # ------------------------------------------------------------------ users

    async def register(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        name: str | None,
        organization_name: str | None,
    ) -> tuple[User, Organization]:
        normalized = email.strip().lower()
        existing = await session.scalar(select(User).where(User.email == normalized))
        if existing is not None:
            raise ConflictError(
                "An account with this email already exists.",
                code="email_taken",
                param="email",
            )

        user = User(
            id=new_id(IdPrefix.USER),
            email=normalized,
            name=name,
            password_hash=self._passwords.hash(password),
        )
        session.add(user)

        organization = await self.create_organization(
            session,
            name=organization_name or f"{name or normalized.split('@')[0]}'s workspace",
            owner=user,
        )
        await session.flush()

        await self.record_audit(
            session,
            organization_id=organization.id,
            actor_type="user",
            actor_id=user.id,
            action="user.registered",
            resource_type="user",
            resource_id=user.id,
        )
        return user, organization

    async def authenticate(self, session: AsyncSession, *, email: str, password: str) -> User:
        normalized = email.strip().lower()
        user = await session.scalar(select(User).where(User.email == normalized))

        # Always run the verifier, even with no user, so response time does not
        # reveal whether the address is registered.
        password_hash = user.password_hash if user else None
        verified = self._passwords.verify(password_hash, password)

        if user is None or not verified or user.status != "active" or user.deleted_at is not None:
            raise AuthenticationError("Email or password is incorrect.")

        if self._passwords.needs_rehash(user.password_hash or ""):
            user.password_hash = self._passwords.hash(password)

        user.last_login_at = datetime.now(UTC)
        return user

    # --------------------------------------------------------------- sessions

    async def start_session(
        self,
        session: AsyncSession,
        *,
        user: User,
        organization_id: str | None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[Session, str]:
        token, token_hash = new_session_token()
        record = Session(
            id=new_id(IdPrefix.SESSION),
            user_id=user.id,
            organization_id=organization_id,
            token_hash=token_hash,
            ip=ip,
            user_agent=user_agent[:500] if user_agent else None,
            expires_at=datetime.now(UTC) + self._session_ttl,
        )
        session.add(record)
        await session.flush()
        return record, token

    async def resolve_session(self, session: AsyncSession, token: str) -> tuple[Session, User]:
        record = await session.scalar(
            select(Session).where(Session.token_hash == hash_session_token(token))
        )
        if record is None or record.revoked_at is not None:
            raise AuthenticationError("Session is not valid.", code="session_invalid")
        if record.expires_at <= datetime.now(UTC):
            raise AuthenticationError("Session has expired.", code="session_expired")

        user = await session.get(User, record.user_id)
        if user is None or user.status != "active":
            raise AuthenticationError("Session is not valid.", code="session_invalid")
        return record, user

    async def revoke_session(self, session: AsyncSession, token: str) -> None:
        await session.execute(
            update(Session)
            .where(Session.token_hash == hash_session_token(token))
            .values(revoked_at=datetime.now(UTC))
        )

    async def switch_organization(
        self, session: AsyncSession, *, session_record: Session, organization_id: str
    ) -> None:
        await self.require_membership(
            session, organization_id=organization_id, user_id=session_record.user_id
        )
        session_record.organization_id = organization_id

    # ---------------------------------------------------------- organizations

    async def create_organization(
        self, session: AsyncSession, *, name: str, owner: User
    ) -> Organization:
        organization_id = new_id(IdPrefix.ORGANIZATION)

        # Identifiers are generated in application code, so the tenant scope can
        # be set before the first tenant-scoped insert. Without this, creating an
        # organization would be the one write that has to bypass row-level
        # security — and that exception would then be available to everything.
        await set_scope(session, organization_id=organization_id, user_id=owner.id)

        organization = Organization(
            id=organization_id,
            slug=await self._unique_slug(session, slugify(name)),
            name=name.strip(),
        )
        session.add(organization)
        session.add(
            OrganizationMember(organization_id=organization.id, user_id=owner.id, role="owner")
        )
        await session.flush()
        return organization

    async def _unique_slug(self, session: AsyncSession, base: str) -> str:
        candidate = base
        for _ in range(5):
            taken = await session.scalar(
                select(Organization.id).where(Organization.slug == candidate)
            )
            if taken is None:
                return candidate
            candidate = f"{base}-{secrets.token_hex(2)}"
        raise ConflictError("Could not allocate an organization slug.", code="slug_exhausted")

    async def organizations_for(
        self, session: AsyncSession, user_id: str
    ) -> list[tuple[Organization, str]]:
        rows = await session.execute(
            select(Organization, OrganizationMember.role)
            .join(
                OrganizationMember,
                OrganizationMember.organization_id == Organization.id,
            )
            .where(OrganizationMember.user_id == user_id, Organization.deleted_at.is_(None))
            .order_by(Organization.created_at)
        )
        return [(organization, role) for organization, role in rows.all()]

    async def require_membership(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        user_id: str,
        minimum_role: str = "viewer",
    ) -> str:
        role = await session.scalar(
            select(OrganizationMember.role).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
            )
        )
        # A non-member is told the organization does not exist: confirming its
        # existence would leak the customer list.
        if role is None:
            raise NotFoundError("Organization not found.", code="organization_not_found")
        if not role_at_least(role, minimum_role):
            from janus_core.errors import AuthorizationError

            raise AuthorizationError(
                "Your role does not permit this action.",
                code="insufficient_role",
                details={"required_role": minimum_role, "your_role": role},
            )
        return role

    async def add_member(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        email: str,
        role: str,
        invited_by: str,
    ) -> OrganizationMember:
        if role not in ROLE_ORDER:
            raise ValidationError(
                "Unknown role.", param="role", details={"allowed": sorted(ROLE_ORDER)}
            )

        user = await session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            # Invitation flows (email delivery, pending invites) land in Phase 9;
            # Phase 1 can only add users who already exist.
            raise NotFoundError(
                "No account exists for this email.",
                code="user_not_found",
                details={"available_from_phase": 9, "capability": "email invitations"},
            )

        existing = await session.scalar(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user.id,
            )
        )
        if existing is not None:
            raise ConflictError("This user is already a member.", code="already_member")

        member = OrganizationMember(
            organization_id=organization_id,
            user_id=user.id,
            role=role,
            invited_by=invited_by,
        )
        session.add(member)
        await session.flush()
        return member

    # -------------------------------------------------------------- api keys

    async def create_api_key(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        created_by: str,
        name: str,
        scopes: list[str],
        mode_ceiling: ExecutionMode | None,
        environment: str = "live",
    ) -> tuple[ApiKey, str]:
        key, prefix, lookup = new_api_key(environment)
        record = ApiKey(
            id=new_id(IdPrefix.API_KEY),
            organization_id=organization_id,
            created_by=created_by,
            name=name.strip(),
            prefix=prefix,
            key_hash=self._passwords.hash_api_key(key),
            lookup_hash=lookup,
            scopes=scopes,
            mode_ceiling=mode_ceiling,
        )
        session.add(record)
        await session.flush()
        return record, key

    async def resolve_api_key(self, session: AsyncSession, key: str) -> ApiKeyIdentity:
        """Authenticate an API key before any tenant context exists.

        ``core.api_keys`` is protected by row-level security, and authentication
        happens before we know which tenant the caller belongs to — so the lookup
        goes through a ``SECURITY DEFINER`` function that returns exactly one
        row's authentication fields and nothing else. This is the only
        cross-tenant read in the service, and it is auditable in one place.
        """
        row = (
            await session.execute(
                text(
                    "SELECT id, organization_id, key_hash, scopes, mode_ceiling, "
                    "revoked_at, expires_at FROM core.authenticate_api_key(:lookup_hash)"
                ),
                {"lookup_hash": api_key_lookup_hash(key)},
            )
        ).one_or_none()

        # A dummy verification keeps the timing of an unknown key close to a
        # known one.
        if row is None:
            self._passwords.verify_api_key(_DUMMY_ARGON2_HASH, key)
            raise AuthenticationError("API key is not valid.", code="invalid_api_key")

        if not self._passwords.verify_api_key(row.key_hash, key):
            raise AuthenticationError("API key is not valid.", code="invalid_api_key")
        if row.revoked_at is not None:
            raise AuthenticationError("API key has been revoked.", code="api_key_revoked")
        if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
            raise AuthenticationError("API key has expired.", code="api_key_expired")

        return ApiKeyIdentity(
            id=row.id,
            organization_id=row.organization_id,
            scopes=list(row.scopes or []),
            mode_ceiling=ExecutionMode(row.mode_ceiling) if row.mode_ceiling else None,
        )

    async def touch_api_key(self, session: AsyncSession, key_id: str) -> None:
        """Record use. Runs inside the caller's tenant context."""
        await session.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=datetime.now(UTC))
        )

    async def revoke_api_key(
        self, session: AsyncSession, *, organization_id: str, key_id: str
    ) -> None:
        # execute() is typed as returning Result; DML always yields a CursorResult,
        # which is the only kind that reports how many rows were affected.
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(ApiKey)
                .where(
                    ApiKey.id == key_id,
                    ApiKey.organization_id == organization_id,
                    ApiKey.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            ),
        )
        if result.rowcount == 0:
            raise NotFoundError("API key not found.", code="api_key_not_found")

    # ---------------------------------------------------------------- audit

    async def record_audit(
        self,
        session: AsyncSession,
        *,
        organization_id: str | None,
        actor_type: str,
        actor_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        ip: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        session.add(
            AuditEvent(
                id=new_id(IdPrefix.AUDIT_EVENT),
                organization_id=organization_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                ip=ip,
                event_metadata=metadata or {},
            )
        )
