"""Liveness and readiness."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from api_app import __version__
from api_app.db import EXPECTED_SCHEMA_VERSION
from api_app.deps import DatabaseDep, GatewayDep

router = APIRouter(tags=["operations"])


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    """Process liveness only: never touches the database or the gateway."""
    return {"status": "ok", "service": "janus-api", "version": __version__}


@router.get("/readyz")
async def readiness(response: Response, db: DatabaseDep, gateway: GatewayDep) -> dict[str, Any]:
    """Dependency readiness.

    The gateway being down is reported but does not make the control plane
    unready: sign-in, organization management, and billing views still work
    without inference, and taking the whole service out of the load balancer
    would turn a partial outage into a total one.

    A missing or unexpected schema does make it unready. Reporting a connected
    database as ready while every query fails on a missing table sends traffic to
    a service that cannot answer, and hides the one problem worth alerting on:
    the deploy ran ahead of its migration.
    """
    database_ok = await db.healthy()
    gateway_ok = await gateway.health()
    version = await db.schema_version() if database_ok else None

    if version is None:
        schema = "unavailable"
    elif version == EXPECTED_SCHEMA_VERSION:
        schema = "ok"
    else:
        schema = f"unexpected: found {version}, expected {EXPECTED_SCHEMA_VERSION}"

    ready = database_ok and schema == "ok"
    if not ready:
        # The status code is what a load balancer acts on; the body is for humans.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if ready else "unavailable",
        "checks": {
            "database": "ok" if database_ok else "unavailable",
            "schema": schema,
            "gateway": "ok" if gateway_ok else "unavailable",
        },
    }
