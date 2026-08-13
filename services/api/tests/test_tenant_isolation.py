"""Tenant isolation, tested at the database level.

These tests exist because "we always filter by organization_id" is a promise no
codebase keeps forever. They assert the database refuses cross-tenant reads even
when the query does not filter at all — which is exactly what a future bug looks
like.
"""

from __future__ import annotations

import pytest
from api_app.models import ApiKey, AuditEvent, Organization, OrganizationMember, User
from janus_core.ids import IdPrefix, new_id
from sqlalchemy import func, select, text


async def _seed_two_tenants(db) -> tuple[str, str, str]:
    """Create two organizations with a member and an API key each."""
    alpha_id = new_id(IdPrefix.ORGANIZATION)
    beta_id = new_id(IdPrefix.ORGANIZATION)
    user_id = new_id(IdPrefix.USER)

    async with db.session() as session:
        session.add_all(
            [
                Organization(id=alpha_id, slug=f"alpha-{alpha_id[-6:]}", name="Alpha"),
                Organization(id=beta_id, slug=f"beta-{beta_id[-6:]}", name="Beta"),
                User(id=user_id, email=f"{user_id}@example.com"),
            ]
        )

    for organization_id in (alpha_id, beta_id):
        async with db.session(organization_id=organization_id) as session:
            session.add_all(
                [
                    OrganizationMember(
                        organization_id=organization_id, user_id=user_id, role="owner"
                    ),
                    ApiKey(
                        id=new_id(IdPrefix.API_KEY),
                        organization_id=organization_id,
                        created_by=user_id,
                        name="key",
                        prefix="jsk_live_ab12",
                        key_hash=f"hash-{organization_id}",
                        lookup_hash=f"lookup-{organization_id}",
                    ),
                    AuditEvent(
                        id=new_id(IdPrefix.AUDIT_EVENT),
                        organization_id=organization_id,
                        actor_type="user",
                        actor_id=user_id,
                        action="test.event",
                        resource_type="test",
                    ),
                ]
            )

    return alpha_id, beta_id, user_id


async def test_unfiltered_query_sees_only_the_current_tenant(db) -> None:
    alpha_id, _, _ = await _seed_two_tenants(db)

    async with db.session(organization_id=alpha_id) as session:
        # Deliberately no WHERE clause: the database is the thing being tested.
        keys = (await session.scalars(select(ApiKey))).all()

    assert len(keys) == 1
    assert keys[0].organization_id == alpha_id


async def test_other_tenants_rows_are_invisible_even_when_named(db) -> None:
    alpha_id, beta_id, _ = await _seed_two_tenants(db)

    async with db.session(organization_id=alpha_id) as session:
        found = await session.scalar(
            select(func.count()).select_from(ApiKey).where(ApiKey.organization_id == beta_id)
        )

    assert found == 0


async def test_audit_events_are_tenant_scoped(db) -> None:
    alpha_id, _, _ = await _seed_two_tenants(db)

    async with db.session(organization_id=alpha_id) as session:
        events = (await session.scalars(select(AuditEvent))).all()

    assert {event.organization_id for event in events} == {alpha_id}


async def test_no_tenant_context_means_no_tenant_rows(db) -> None:
    """The safe default: unset context reads nothing, rather than everything."""
    await _seed_two_tenants(db)

    async with db.session() as session:
        keys = (await session.scalars(select(ApiKey))).all()

    assert keys == []


async def test_writing_into_another_tenant_is_refused(db) -> None:
    alpha_id, beta_id, user_id = await _seed_two_tenants(db)

    with pytest.raises(Exception) as excinfo:
        async with db.session(organization_id=alpha_id) as session:
            session.add(
                ApiKey(
                    id=new_id(IdPrefix.API_KEY),
                    organization_id=beta_id,  # not the current tenant
                    created_by=user_id,
                    name="smuggled",
                    prefix="jsk_live_ffff",
                    key_hash="hash-smuggled",
                    lookup_hash="lookup-smuggled",
                )
            )

    assert "row-level security" in str(excinfo.value).lower()


async def test_membership_is_visible_by_user_across_tenants(db) -> None:
    """Listing your own organizations is legitimate and needs no RLS exception."""
    alpha_id, beta_id, user_id = await _seed_two_tenants(db)

    async with db.session(user_id=user_id) as session:
        memberships = (await session.scalars(select(OrganizationMember))).all()

    assert {member.organization_id for member in memberships} == {alpha_id, beta_id}


async def test_membership_of_other_users_is_not_visible(db) -> None:
    await _seed_two_tenants(db)
    other_user = new_id(IdPrefix.USER)

    async with db.session(user_id=other_user) as session:
        memberships = (await session.scalars(select(OrganizationMember))).all()

    assert memberships == []


async def test_service_role_cannot_bypass_row_level_security(db) -> None:
    """The role the service connects as must not hold BYPASSRLS."""
    async with db.session() as session:
        row = (
            await session.execute(
                text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user")
            )
        ).one()

    assert row.rolbypassrls is False
    assert row.rolsuper is False


async def test_tenant_context_does_not_survive_the_transaction(db) -> None:
    """SET LOCAL, not SET: a pooled connection must not carry context onward."""
    alpha_id, _, _ = await _seed_two_tenants(db)

    async with db.session(organization_id=alpha_id) as session:
        assert (await session.scalars(select(ApiKey))).all()

    async with db.session() as session:
        setting = await session.scalar(
            text("SELECT current_setting('janus.organization_id', true)")
        )
        assert setting in (None, "")


async def test_audit_events_cannot_be_updated_or_deleted(db) -> None:
    """Append-only is a grant, not a convention."""
    alpha_id, _, _ = await _seed_two_tenants(db)

    for statement in (
        "UPDATE core.audit_events SET action = 'tampered'",
        "DELETE FROM core.audit_events",
    ):
        with pytest.raises(Exception) as excinfo:
            async with db.session(organization_id=alpha_id) as session:
                await session.execute(text(statement))
        assert "permission denied" in str(excinfo.value).lower()
