"""Weighted scoring for model routing (model-routing.md section 5)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from janus_schemas.chat import RoutingRequirements
from janus_schemas.common import CostClass, HealthState, LatencyClass, PrivacyLevel

from gateway_app.health import HealthTracker
from gateway_app.registry.records import DeploymentRecord, ModelRecord

_COST_PENALTY: dict[CostClass, float] = {
    CostClass.FREE: 0.0,
    CostClass.LOW: 0.15,
    CostClass.MEDIUM: 0.35,
    CostClass.HIGH: 0.55,
    CostClass.FIXED: 0.75,
}

_LATENCY_BONUS: dict[LatencyClass, float] = {
    LatencyClass.LOW: 0.25,
    LatencyClass.MEDIUM: 0.12,
    LatencyClass.HIGH: 0.0,
}


class WeightProfile(StrEnum):
    BALANCED = "balanced"
    QUALITY_FIRST = "quality_first"
    SPEED_FIRST = "speed_first"
    COST_OPTIMIZED = "cost_optimized"
    PRIVACY_FIRST = "privacy_first"


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    capability: float
    language: float
    privacy: float
    quality: float
    latency: float
    availability: float
    cost: float


_PROFILES: dict[WeightProfile, ScoreWeights] = {
    WeightProfile.BALANCED: ScoreWeights(0.25, 0.10, 0.10, 0.20, 0.15, 0.15, 0.15),
    WeightProfile.QUALITY_FIRST: ScoreWeights(0.20, 0.10, 0.10, 0.35, 0.10, 0.10, 0.05),
    WeightProfile.SPEED_FIRST: ScoreWeights(0.15, 0.05, 0.05, 0.10, 0.35, 0.25, 0.05),
    WeightProfile.COST_OPTIMIZED: ScoreWeights(0.20, 0.05, 0.05, 0.10, 0.10, 0.10, 0.40),
    WeightProfile.PRIVACY_FIRST: ScoreWeights(0.15, 0.05, 0.35, 0.15, 0.10, 0.10, 0.10),
}


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    total: float
    components: dict[str, float]


def score_candidate(
    model: ModelRecord,
    deployment: DeploymentRecord,
    requirements: RoutingRequirements,
    health: HealthTracker,
    *,
    profile: WeightProfile = WeightProfile.BALANCED,
) -> ScoreBreakdown:
    """Higher is better. All component values are normalized to 0..1."""

    capability = _capability_match(model, deployment, requirements)
    language = _language_match(model, requirements)
    privacy = _privacy_match(deployment.privacy_level)
    quality = _quality_prior(model)
    latency = _latency_bonus(model)
    availability = _availability_score(health, deployment.key)
    cost = _COST_PENALTY.get(model.cost_class, 0.35)

    components = {
        "capability": capability,
        "language": language,
        "privacy": privacy,
        "quality": quality,
        "latency": latency,
        "availability": availability,
        "cost_penalty": cost,
    }

    weights = _PROFILES[profile]
    total = (
        weights.capability * capability
        + weights.language * language
        + weights.privacy * privacy
        + weights.quality * quality
        + weights.latency * latency
        + weights.availability * availability
        - weights.cost * cost
        + deployment.priority / 1000.0
    )
    return ScoreBreakdown(total=total, components=components)


def _capability_match(
    model: ModelRecord, deployment: DeploymentRecord, requirements: RoutingRequirements
) -> float:
    required = requirements.capabilities
    if not required:
        return 1.0
    caps = model.capabilities_for(deployment)
    if not caps.satisfies(required):
        return 0.0
    matched = sum(1 for name in required if getattr(caps, name, False))
    return matched / len(required)


def _language_match(model: ModelRecord, requirements: RoutingRequirements) -> float:
    requested = requirements.languages
    if not requested:
        return 1.0
    if not model.languages:
        return 0.5
    overlap = set(requested) & set(model.languages)
    return len(overlap) / len(requested)


def _privacy_match(privacy: PrivacyLevel) -> float:
    return {
        PrivacyLevel.LOCAL: 1.0,
        PrivacyLevel.PRIVATE: 0.85,
        PrivacyLevel.PROVIDER: 0.5,
    }.get(privacy, 0.5)


def _quality_prior(model: ModelRecord) -> float:
    from janus_schemas.common import ModelTier

    tier_scores = {
        ModelTier.RECOMMENDED: 1.0,
        ModelTier.FRONTIER: 0.95,
        ModelTier.OPEN_SOURCE: 0.7,
        ModelTier.EXPERIMENTAL: 0.4,
        ModelTier.DEPRECATED: 0.1,
    }
    base = tier_scores.get(model.tier, 0.5)
    return base if model.metadata_verified else base * 0.85


def _latency_bonus(model: ModelRecord) -> float:
    return _LATENCY_BONUS.get(model.latency_class, 0.1)


def _availability_score(health: HealthTracker, deployment_key: str) -> float:
    state = health.state_for(deployment_key)
    if state is HealthState.READY:
        return 1.0
    if state is HealthState.DEGRADED:
        return 0.6
    if state is HealthState.WARMING:
        return 0.3
    return 0.0
