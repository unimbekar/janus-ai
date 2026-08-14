"""Weighted routing scores."""

from __future__ import annotations

from gateway_app.health import HealthTracker
from gateway_app.registry.records import DeploymentRecord, ModelRecord
from gateway_app.router.scoring import WeightProfile, score_candidate
from janus_schemas.chat import RoutingRequirements
from janus_schemas.common import CostClass, DeploymentType, ModelTier, PrivacyLevel, Protocol
from janus_schemas.models import ModelCapabilities


def _model_and_deployment(
    *,
    slug: str,
    tier: ModelTier,
    cost: CostClass,
    priority: int,
    privacy: PrivacyLevel,
) -> tuple[ModelRecord, DeploymentRecord]:
    model = ModelRecord(
        slug=slug,
        display_name=slug,
        provider="janus",
        type="chat",  # type: ignore[arg-type]
        context_window=8192,
        capabilities=ModelCapabilities(streaming=True, reasoning=True),
        tier=tier,
        cost_class=cost,
        metadata_verified=True,
        deployments=(),
    )
    deployment = DeploymentRecord(
        key=f"{slug}-dep",
        model_slug=slug,
        backend="mock",
        protocol=Protocol.NATIVE,
        deployment_type=DeploymentType.LOCAL_DEV,
        privacy_level=privacy,
        priority=priority,
    )
    return model, deployment


def test_free_tier_scores_higher_under_cost_optimized_profile() -> None:
    health = HealthTracker()
    free_model, free_dep = _model_and_deployment(
        slug="janus/free",
        tier=ModelTier.EXPERIMENTAL,
        cost=CostClass.FREE,
        priority=10,
        privacy=PrivacyLevel.LOCAL,
    )
    premium_model, premium_dep = _model_and_deployment(
        slug="openai/gpt-4",
        tier=ModelTier.FRONTIER,
        cost=CostClass.HIGH,
        priority=50,
        privacy=PrivacyLevel.PROVIDER,
    )
    free = score_candidate(
        free_model, free_dep, RoutingRequirements(), health, profile=WeightProfile.COST_OPTIMIZED
    )
    premium = score_candidate(
        premium_model,
        premium_dep,
        RoutingRequirements(),
        health,
        profile=WeightProfile.COST_OPTIMIZED,
    )
    assert free.total > premium.total


def test_capability_requirement_zeroes_ineligible_match() -> None:
    health = HealthTracker()
    model, deployment = _model_and_deployment(
        slug="janus/mock",
        tier=ModelTier.EXPERIMENTAL,
        cost=CostClass.FREE,
        priority=10,
        privacy=PrivacyLevel.LOCAL,
    )
    score = score_candidate(model, deployment, RoutingRequirements(capabilities=["vision"]), health)
    assert score.components["capability"] == 0.0
