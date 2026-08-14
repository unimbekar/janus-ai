"""Usage, audit, policies, and deployment health — enterprise-facing surfaces."""

from __future__ import annotations

from fastapi import APIRouter, Query
from janus_core.ids import IdPrefix, new_id
from janus_schemas.common import ExecutionMode
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from api_app.deps import (
    ClassificationDep,
    GatewayDep,
    ModeDep,
    PrincipalDep,
    RequestIdDep,
    SessionDep,
)
from api_app.models import AuditEvent, Policy

router = APIRouter(prefix="/v1", tags=["ops"])


class CreatePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: ExecutionMode | None = None
    weight_profile: str | None = None
    max_cost_usd_per_day: float | None = Field(default=None, gt=0)


@router.get("/audit-events")
async def list_audit_events(
    principal: PrincipalDep,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    principal.require_role("admin")
    rows = await session.scalars(
        select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    )
    return {
        "data": [
            {
                "id": event.id,
                "action": event.action,
                "resource_type": event.resource_type,
                "resource_id": event.resource_id,
                "actor_id": event.actor_id,
                "created_at": event.created_at,
            }
            for event in rows
        ]
    }


@router.get("/usage")
async def usage_summary(principal: PrincipalDep, session: SessionDep) -> dict:
    result = await session.execute(
        text(
            """
            SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cost_usd), 0) AS cost_usd,
                   COUNT(*) AS requests
            FROM telemetry.usage_records
            """
        )
    )
    row = result.mappings().one()
    return {
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "cost_usd": str(row["cost_usd"]),
        "requests": int(row["requests"]),
    }


@router.get("/policies")
async def list_policies(principal: PrincipalDep, session: SessionDep) -> dict:
    rows = await session.scalars(
        select(Policy).where(Policy.is_active.is_(True)).order_by(Policy.created_at.desc())
    )
    return {
        "data": [
            {
                "id": policy.id,
                "scope": policy.scope,
                "mode": policy.mode,
                "weight_profile": policy.weight_profile,
                "limits": policy.limits,
                "version": policy.version,
            }
            for policy in rows
        ]
    }


@router.post("/policies")
async def create_policy(
    body: CreatePolicyRequest, principal: PrincipalDep, session: SessionDep
) -> dict:
    principal.require_role("admin")
    policy = Policy(
        id=new_id(IdPrefix.POLICY),
        scope="organization",
        scope_id=principal.organization_id,
        organization_id=principal.organization_id,
        mode=body.mode,
        weight_profile=body.weight_profile,
        limits={"max_cost_usd_per_day": body.max_cost_usd_per_day}
        if body.max_cost_usd_per_day
        else {},
        fallback={"enabled": True, "max_attempts": 3},
        created_by=principal.user_id,
    )
    session.add(policy)
    await session.flush()
    return {"id": policy.id, "scope": policy.scope, "mode": policy.mode}


@router.get("/deployments")
async def list_deployments(
    principal: PrincipalDep,
    gateway: GatewayDep,
    mode: ModeDep,
    classification: ClassificationDep,
    request_id: RequestIdDep,
) -> dict:
    """Public deployment health: no endpoints, no credentials."""
    models = await gateway.list_models(
        organization_id=principal.organization_id,
        request_id=request_id,
        mode=mode,
        classification=classification,
    )
    deployments = []
    for model in models.get("data") or []:
        janus = model.get("janus") or {}
        for item in janus.get("deployments") or []:
            deployments.append(
                {
                    "model": model.get("id"),
                    "key": item.get("key"),
                    "privacy": item.get("privacy"),
                    "availability": item.get("availability"),
                    "accelerator": item.get("accelerator"),
                    "region": item.get("region"),
                }
            )
    return {"data": deployments}
