"""Persist routing decisions and usage records (Phase 3)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from janus_core.ids import IdPrefix, new_id
from janus_core.logging import get_logger
from janus_schemas.chat import Usage
from sqlalchemy import text

from gateway_app.db import GatewayDatabase
from gateway_app.registry.records import ModelRecord
from gateway_app.router.resolver import Candidate, ResolutionResult

logger = get_logger(__name__)


@dataclass(slots=True)
class UsageWrite:
    request_id: str
    organization_id: str
    model: ModelRecord
    deployment_key: str
    usage: Usage
    operation: str = "chat"
    user_id: str | None = None
    api_key_id: str | None = None
    conversation_id: str | None = None
    ttft_ms: int | None = None
    total_ms: int | None = None
    cost_usd: str = "0"
    cost_basis: str = "cost_class_estimate"
    fallback_used: bool = False
    usage_estimated: bool = False
    error_code: str | None = None


class TelemetryWriter:
    def __init__(self, db: GatewayDatabase | None) -> None:
        self._db = db

    @property
    def enabled(self) -> bool:
        return self._db is not None

    async def record_routing_decision(
        self,
        *,
        request_id: str,
        organization_id: str,
        resolution: ResolutionResult,
        selected: Candidate,
        requested_model: str,
        mode: str,
        classification: str,
        requirements: dict[str, Any],
        user_id: str | None = None,
        api_key_id: str | None = None,
        conversation_id: str | None = None,
        decision_ms: int | None = None,
        fallback_used: bool = False,
        error_code: str | None = None,
    ) -> None:
        if not self._db:
            return

        candidates_payload = []
        for deployment_key, breakdown in resolution.scores:
            candidates_payload.append(
                {
                    "deployment": deployment_key,
                    "score": round(breakdown.total, 6),
                    "components": {k: round(v, 4) for k, v in breakdown.components.items()},
                }
            )
        for ref, reason in resolution.excluded:
            candidates_payload.append({"deployment": ref, "excluded_reason": reason.value})

        async with self._db.session(
            organization_id=organization_id, user_id=user_id, api_key_id=api_key_id
        ) as session:
            await session.execute(
                text(
                    """
                    INSERT INTO telemetry.routing_decisions (
                      id, request_id, organization_id, user_id, api_key_id, conversation_id,
                      requested_model, mode, classification, requirements, weight_profile,
                      candidates, selected_model_slug, selected_deployment_key, selected_provider,
                      routing_reason, routing_explanation, fallback_used, decision_ms, error_code
                    ) VALUES (
                      :id, :request_id, :organization_id, :user_id, :api_key_id, :conversation_id,
                      :requested_model, :mode, :classification, CAST(:requirements AS jsonb),
                      :weight_profile, CAST(:candidates AS jsonb),
                      :selected_model_slug, :selected_deployment_key, :selected_provider,
                      :routing_reason, :routing_explanation, :fallback_used,
                      :decision_ms, :error_code
                    )
                    ON CONFLICT (request_id) DO NOTHING
                    """
                ),
                {
                    "id": new_id(IdPrefix.DECISION),
                    "request_id": request_id,
                    "organization_id": organization_id,
                    "user_id": user_id,
                    "api_key_id": api_key_id,
                    "conversation_id": conversation_id,
                    "requested_model": requested_model,
                    "mode": mode,
                    "classification": classification,
                    "requirements": json.dumps(requirements),
                    "weight_profile": resolution.weight_profile,
                    "candidates": json.dumps(candidates_payload),
                    "selected_model_slug": selected.model.slug,
                    "selected_deployment_key": selected.deployment.key,
                    "selected_provider": selected.model.provider,
                    "routing_reason": resolution.routing_reason,
                    "routing_explanation": resolution.explanation,
                    "fallback_used": fallback_used,
                    "decision_ms": decision_ms,
                    "error_code": error_code,
                },
            )

    async def record_usage(self, payload: UsageWrite) -> None:
        if not self._db:
            return

        async with self._db.session(
            organization_id=payload.organization_id,
            user_id=payload.user_id,
            api_key_id=payload.api_key_id,
        ) as session:
            await session.execute(
                text(
                    """
                    INSERT INTO telemetry.usage_records (
                      id, request_id, organization_id, user_id, api_key_id, conversation_id,
                      model_slug, deployment_key, provider, operation,
                      input_tokens, output_tokens, ttft_ms, total_ms, cost_usd, cost_basis,
                      fallback_used, usage_estimated, error_code
                    ) VALUES (
                      :id, :request_id, :organization_id, :user_id, :api_key_id, :conversation_id,
                      :model_slug, :deployment_key, :provider, :operation,
                      :input_tokens, :output_tokens, :ttft_ms, :total_ms, :cost_usd, :cost_basis,
                      :fallback_used, :usage_estimated, :error_code
                    )
                    """
                ),
                {
                    "id": new_id(IdPrefix.USAGE),
                    "request_id": payload.request_id,
                    "organization_id": payload.organization_id,
                    "user_id": payload.user_id,
                    "api_key_id": payload.api_key_id,
                    "conversation_id": payload.conversation_id,
                    "model_slug": payload.model.slug,
                    "deployment_key": payload.deployment_key,
                    "provider": payload.model.provider,
                    "operation": payload.operation,
                    "input_tokens": payload.usage.prompt_tokens,
                    "output_tokens": payload.usage.completion_tokens,
                    "ttft_ms": payload.ttft_ms,
                    "total_ms": payload.total_ms,
                    "cost_usd": payload.cost_usd,
                    "cost_basis": payload.cost_basis,
                    "fallback_used": payload.fallback_used,
                    "usage_estimated": payload.usage_estimated,
                    "error_code": payload.error_code,
                },
            )
