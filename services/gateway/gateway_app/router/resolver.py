"""Model resolution: request + policy → an ordered candidate list.

Phase 1 scope is deliberate. Everything that makes a candidate *ineligible* is
implemented now — mode, privacy, classification, region, provider, capability,
context, and health — because those are correctness and security rules that must
never be added later as an afterthought. What is *not* here is the weighted
scoring model from docs/model-routing.md §5: ranking in Phase 1 is a stable,
explainable ordering by health, declared priority, and tier. Phase 3 replaces
``_rank`` with scoring, and nothing else in this file needs to change.

Two invariants hold in both phases:
  - eligibility is a filter, never a preference: a candidate that violates a
    constraint is excluded, not deprioritized;
  - an explicit request is still filtered — naming a model or deployment cannot
    route around a policy, and an ineligible pin gets a typed error rather than a
    silent substitution.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from janus_core.errors import NotFoundError, PolicyViolationError
from janus_schemas.chat import RoutingConstraints, RoutingRequirements
from janus_schemas.common import (
    Classification,
    DeploymentType,
    ExecutionMode,
    ModelTier,
    PrivacyLevel,
)

from gateway_app.health import HealthTracker
from gateway_app.registry.records import DeploymentRecord, ModelRecord, Registry

_ALIAS_PREFIX = "janus/"
_DEPLOYMENT_SEPARATOR = "@"
_AUTO = "auto"

_TIER_ORDER: dict[ModelTier, int] = {
    ModelTier.RECOMMENDED: 0,
    ModelTier.FRONTIER: 1,
    ModelTier.OPEN_SOURCE: 2,
    ModelTier.EXPERIMENTAL: 3,
    ModelTier.DEPRECATED: 4,
}

_JANUS_HOSTED_TYPES = frozenset({DeploymentType.JANUS_GPU, DeploymentType.JANUS_CPU})


class ExclusionReason(StrEnum):
    """Why a candidate was removed. Aggregated into a safe explanation."""

    DISABLED = "disabled"
    MODEL_INACTIVE = "model_inactive"
    MODE = "execution_mode"
    CLASSIFICATION = "data_classification"
    REGION = "region_constraint"
    PROVIDER = "provider_constraint"
    CAPABILITY = "capability_missing"
    LANGUAGE = "language_unsupported"
    CONTEXT = "context_too_small"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class Candidate:
    model: ModelRecord
    deployment: DeploymentRecord

    @property
    def ref(self) -> str:
        return self.deployment.ref


@dataclass(frozen=True, slots=True)
class ResolutionRequest:
    """Everything resolution needs, already policy-resolved by the caller."""

    model: str
    mode: ExecutionMode = ExecutionMode.AUTO
    classification: Classification = Classification.INTERNAL
    requirements: RoutingRequirements = field(default_factory=RoutingRequirements)
    constraints: RoutingConstraints = field(default_factory=RoutingConstraints)


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    candidates: tuple[Candidate, ...]
    routing_reason: str
    explanation: str
    pinned: bool = False
    excluded: tuple[tuple[str, ExclusionReason], ...] = ()

    @property
    def primary(self) -> Candidate:
        return self.candidates[0]


class ModelResolver:
    def __init__(self, health: HealthTracker) -> None:
        self._health = health

    # ------------------------------------------------------------- entry point

    def resolve(self, registry: Registry, request: ResolutionRequest) -> ResolutionResult:
        pool, routing_reason, pinned = self._candidate_pool(registry, request)

        eligible: list[Candidate] = []
        excluded: list[tuple[str, ExclusionReason]] = []
        for candidate in pool:
            reason = self._first_exclusion(candidate, request)
            if reason is None:
                eligible.append(candidate)
            else:
                excluded.append((candidate.ref, reason))

        if not eligible:
            raise self._no_eligible_model(request, excluded, pinned)

        ordered = tuple(sorted(eligible, key=self._rank))
        return ResolutionResult(
            candidates=ordered,
            routing_reason=routing_reason,
            explanation=self._explain(ordered[0], request, routing_reason),
            pinned=pinned,
            excluded=tuple(excluded),
        )

    def eligible_candidates(
        self, registry: Registry, request: ResolutionRequest
    ) -> list[Candidate]:
        """Eligible candidates without raising — used to filter the catalog.

        The model list a caller sees and the models a caller can actually use are
        produced by the same rules, so the catalog can never advertise something
        policy would refuse.
        """
        return [
            candidate
            for candidate in self._all_candidates(registry)
            if self._first_exclusion(candidate, request) is None
        ]

    # -------------------------------------------------------------- candidates

    def _candidate_pool(
        self, registry: Registry, request: ResolutionRequest
    ) -> tuple[list[Candidate], str, bool]:
        spec = request.model.strip()

        if _DEPLOYMENT_SEPARATOR in spec:
            slug, _, deployment_key = spec.partition(_DEPLOYMENT_SEPARATOR)
            entry = registry.deployments_by_key.get(deployment_key)
            if entry is None or entry[1].model_slug != slug:
                raise NotFoundError(
                    "The requested deployment does not exist.",
                    code="deployment_not_found",
                    details={"requested": spec},
                )
            return [Candidate(*entry)], "explicit_deployment", True

        if spec == _AUTO or not spec:
            return self._all_candidates(registry), "auto", False

        model = registry.get_model(spec)
        if model is not None:
            return (
                [Candidate(model, deployment) for deployment in model.deployments],
                "explicit_model",
                False,
            )

        if spec.startswith(_ALIAS_PREFIX):
            aliased = registry.resolve_alias(spec)
            if aliased:
                return (
                    [Candidate(m, d) for m in aliased for d in m.deployments],
                    "capability_alias",
                    False,
                )

        raise NotFoundError(
            "The requested model is not available.",
            code="model_not_found",
            details={"requested": spec},
        )

    @staticmethod
    def _all_candidates(registry: Registry) -> list[Candidate]:
        return [
            Candidate(model, deployment)
            for model in registry.models
            for deployment in model.deployments
        ]

    # ------------------------------------------------------------ eligibility

    def _first_exclusion(
        self, candidate: Candidate, request: ResolutionRequest
    ) -> ExclusionReason | None:
        model, deployment = candidate.model, candidate.deployment

        if not deployment.enabled:
            return ExclusionReason.DISABLED
        if model.status != "active":
            return ExclusionReason.MODEL_INACTIVE
        if not self._mode_allows(request.mode, deployment):
            return ExclusionReason.MODE
        if not self._classification_allows(request.classification, deployment):
            return ExclusionReason.CLASSIFICATION

        constraints = request.constraints
        if constraints.regions and (deployment.region or "") not in constraints.regions:
            return ExclusionReason.REGION
        if constraints.providers and model.provider not in constraints.providers:
            return ExclusionReason.PROVIDER
        if model.provider in constraints.exclude_providers:
            return ExclusionReason.PROVIDER

        requirements = request.requirements
        if requirements.capabilities:
            capabilities = model.capabilities_for(deployment)
            if not capabilities.satisfies(requirements.capabilities):
                return ExclusionReason.CAPABILITY
        if (
            requirements.languages
            and model.languages
            and not set(requirements.languages).issubset(set(model.languages))
        ):
            return ExclusionReason.LANGUAGE

        context = deployment.max_context or model.context_window
        if requirements.min_context and context < requirements.min_context:
            return ExclusionReason.CONTEXT

        if not self._health.state_for(deployment.key).is_routable:
            return ExclusionReason.UNHEALTHY

        return None

    @staticmethod
    def _mode_allows(mode: ExecutionMode, deployment: DeploymentRecord) -> bool:
        privacy = deployment.privacy_level

        if mode is ExecutionMode.CLOUD:
            return privacy is PrivacyLevel.PROVIDER
        if mode is ExecutionMode.PRIVATE:
            return privacy in (PrivacyLevel.PRIVATE, PrivacyLevel.LOCAL)
        if mode is ExecutionMode.SOVEREIGN:
            # Sovereign means inference happens only on infrastructure Janus or
            # the operator controls — provider clouds are structurally excluded.
            return privacy is PrivacyLevel.PRIVATE and (
                deployment.deployment_type in _JANUS_HOSTED_TYPES
            )
        if mode is ExecutionMode.OFFLINE:
            return privacy is PrivacyLevel.LOCAL
        return True  # AUTO

    @staticmethod
    def _classification_allows(
        classification: Classification, deployment: DeploymentRecord
    ) -> bool:
        # CONFIDENTIAL and above never reach an external provider, whatever the
        # requested mode said.
        if classification.rank >= Classification.CONFIDENTIAL.rank:
            return deployment.privacy_level is not PrivacyLevel.PROVIDER
        return True

    # --------------------------------------------------------------- ordering

    def _rank(self, candidate: Candidate) -> tuple[int, int, int, str]:
        """Stable Phase 1 ordering. Replaced by weighted scoring in Phase 3."""
        state = self._health.state_for(candidate.deployment.key)
        health_rank = 0 if state.name == "READY" else 1
        return (
            health_rank,
            candidate.deployment.priority,
            _TIER_ORDER.get(candidate.model.tier, 9),
            candidate.deployment.key,
        )

    # ------------------------------------------------------------ explanation

    @staticmethod
    def _explain(candidate: Candidate, request: ResolutionRequest, routing_reason: str) -> str:
        """A safe, generated sentence — never model reasoning, never endpoints."""
        privacy = candidate.deployment.privacy_level
        clauses: list[str] = []

        if routing_reason == "explicit_deployment":
            clauses.append("you pinned this deployment")
        elif routing_reason == "explicit_model":
            clauses.append(f"you selected {candidate.model.display_name}")
        elif routing_reason == "capability_alias":
            clauses.append(f"the {request.model} class was requested")
        else:
            clauses.append("it is the best available match for this request")

        if request.requirements.capabilities:
            clauses.append("it provides " + ", ".join(request.requirements.capabilities))
        if request.mode is not ExecutionMode.AUTO:
            clauses.append(f"your {request.mode.value} policy applies")
        if privacy is not PrivacyLevel.PROVIDER:
            clauses.append("inference stays on infrastructure we operate")

        return f"Selected {candidate.model.display_name} because " + "; ".join(clauses) + "."

    @staticmethod
    def _no_eligible_model(
        request: ResolutionRequest,
        excluded: list[tuple[str, ExclusionReason]],
        pinned: bool,
    ) -> PolicyViolationError:
        counts = Counter(reason.value for _, reason in excluded)
        dominant = counts.most_common(1)[0][0] if counts else "no_candidates"

        hints = {
            ExclusionReason.MODE.value: (
                "Relax the execution mode, or enable a Janus-hosted deployment."
            ),
            ExclusionReason.CLASSIFICATION.value: (
                "This data classification requires a private deployment; enable one or "
                "reclassify the request."
            ),
            ExclusionReason.REGION.value: ("No eligible deployment exists in the required region."),
            ExclusionReason.PROVIDER.value: "Provider constraints excluded every candidate.",
            ExclusionReason.CAPABILITY.value: (
                "No permitted model provides the requested capabilities."
            ),
            ExclusionReason.LANGUAGE.value: (
                "No permitted model declares support for this language."
            ),
            ExclusionReason.CONTEXT.value: (
                "The request needs a larger context window than any permitted model."
            ),
            ExclusionReason.UNHEALTHY.value: (
                "Every permitted deployment is currently unavailable."
            ),
        }

        return PolicyViolationError(
            "No model satisfies this request under the applicable policy."
            if not pinned
            else "The pinned deployment is not eligible for this request.",
            code="deployment_ineligible" if pinned else "no_eligible_model",
            details={
                "requested_model": request.model,
                "mode": request.mode.value,
                "classification": request.classification.value,
                "excluded_counts": dict(counts),
                "hint": hints.get(dominant, "Adjust the request or the applicable policy."),
            },
        )
