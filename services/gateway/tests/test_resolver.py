"""Resolution and eligibility.

These are the tests that protect the platform's promises: a policy cannot be
routed around, and an explicit request is still filtered.
"""

from __future__ import annotations

import pytest
from gateway_app.router.resolver import ExclusionReason, ResolutionRequest
from janus_core.errors import NotFoundError, PolicyViolationError
from janus_schemas.chat import RoutingConstraints, RoutingRequirements
from janus_schemas.common import Classification, ExecutionMode, HealthState, ModelType, PrivacyLevel


def request_for(**overrides) -> ResolutionRequest:
    defaults: dict = {"model": "auto", "model_type": ModelType.CHAT}
    return ResolutionRequest(**{**defaults, **overrides})


def test_auto_returns_candidates_ordered_by_score(registry, resolver) -> None:
    result = resolver.resolve(registry, request_for())

    assert result.routing_reason == "auto"
    assert [candidate.deployment.key for candidate in result.candidates] == [
        "mock-small-local",
        "mock-reasoning-private",
    ]


def test_hindi_auto_prefers_the_indic_model(registry, resolver) -> None:
    result = resolver.resolve(
        registry,
        request_for(requirements=RoutingRequirements(languages=["hi"], capabilities=["indic"])),
    )
    assert result.primary.model.slug == "janus/mock-reasoning"
    assert result.scores[0][1].components["language"] == 1.0


def test_explicit_model_selects_its_deployments(registry, resolver) -> None:
    result = resolver.resolve(registry, request_for(model="janus/mock-reasoning"))

    assert result.routing_reason == "explicit_model"
    assert result.primary.deployment.key == "mock-reasoning-private"


def test_capability_alias_resolves_to_the_class(registry, resolver) -> None:
    result = resolver.resolve(registry, request_for(model="janus/reasoning"))

    assert result.routing_reason == "capability_alias"
    assert result.primary.model.slug == "janus/mock-reasoning"


def test_unknown_model_is_not_found(registry, resolver) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        resolver.resolve(registry, request_for(model="does-not-exist"))

    assert excinfo.value.code == "model_not_found"


def test_deployment_pin_selects_exactly_one(registry, resolver) -> None:
    result = resolver.resolve(registry, request_for(model="janus/mock-small@mock-small-local"))

    assert result.pinned is True
    assert len(result.candidates) == 1


def test_deployment_pin_with_wrong_model_is_rejected(registry, resolver) -> None:
    with pytest.raises(NotFoundError):
        resolver.resolve(registry, request_for(model="janus/mock-reasoning@mock-small-local"))


def test_pinned_deployment_still_passes_policy(registry, resolver) -> None:
    """A caller cannot pin their way past a restriction."""
    with pytest.raises(PolicyViolationError) as excinfo:
        resolver.resolve(
            registry,
            request_for(
                model="janus/mock-small@mock-small-local",
                mode=ExecutionMode.CLOUD,  # the pinned deployment is local
            ),
        )

    assert excinfo.value.code == "deployment_ineligible"


def test_cloud_mode_excludes_non_provider_deployments(registry, resolver) -> None:
    with pytest.raises(PolicyViolationError) as excinfo:
        resolver.resolve(registry, request_for(mode=ExecutionMode.CLOUD))

    # No provider-cloud deployment exists in the test environment at all.
    assert excinfo.value.details["excluded_counts"][ExclusionReason.MODE.value] == 2


def test_private_mode_keeps_private_and_local(registry, resolver) -> None:
    result = resolver.resolve(registry, request_for(mode=ExecutionMode.PRIVATE))

    privacies = {candidate.deployment.privacy_level for candidate in result.candidates}
    assert privacies <= {PrivacyLevel.PRIVATE, PrivacyLevel.LOCAL}


def test_sovereign_mode_requires_janus_hosted(registry, resolver) -> None:
    result = resolver.resolve(registry, request_for(mode=ExecutionMode.SOVEREIGN))

    assert [candidate.deployment.key for candidate in result.candidates] == [
        "mock-reasoning-private"
    ]


def test_offline_mode_keeps_only_local(registry, resolver) -> None:
    result = resolver.resolve(registry, request_for(mode=ExecutionMode.OFFLINE))

    assert [candidate.deployment.key for candidate in result.candidates] == ["mock-small-local"]


@pytest.mark.parametrize("classification", [Classification.CONFIDENTIAL, Classification.RESTRICTED])
def test_sensitive_data_never_reaches_a_provider(registry, resolver, classification) -> None:
    result = resolver.resolve(registry, request_for(classification=classification))

    assert all(
        candidate.deployment.privacy_level is not PrivacyLevel.PROVIDER
        for candidate in result.candidates
    )


def test_capability_requirement_filters(registry, resolver) -> None:
    result = resolver.resolve(
        registry,
        request_for(requirements=RoutingRequirements(capabilities=["long_context", "documents"])),
    )

    assert result.primary.model.slug == "janus/mock-reasoning"


def test_impossible_capability_is_a_policy_error_not_a_downgrade(registry, resolver) -> None:
    with pytest.raises(PolicyViolationError) as excinfo:
        resolver.resolve(
            registry, request_for(requirements=RoutingRequirements(capabilities=["vision"]))
        )

    assert excinfo.value.code == "no_eligible_model"
    assert excinfo.value.details["excluded_counts"][ExclusionReason.CAPABILITY.value] == 2


def test_language_requirement_filters(registry, resolver) -> None:
    result = resolver.resolve(
        registry, request_for(requirements=RoutingRequirements(languages=["hi"]))
    )

    assert result.primary.model.slug == "janus/mock-reasoning"


def test_context_requirement_filters(registry, resolver) -> None:
    result = resolver.resolve(
        registry, request_for(requirements=RoutingRequirements(min_context=100000))
    )

    assert result.primary.deployment.key == "mock-reasoning-private"


def test_region_constraint_filters(registry, resolver) -> None:
    result = resolver.resolve(
        registry, request_for(constraints=RoutingConstraints(regions=["us-east-1"]))
    )

    assert [candidate.deployment.key for candidate in result.candidates] == [
        "mock-reasoning-private"
    ]


def test_provider_exclusion_filters(registry, resolver) -> None:
    with pytest.raises(PolicyViolationError):
        resolver.resolve(
            registry, request_for(constraints=RoutingConstraints(exclude_providers=["janus"]))
        )


def test_unhealthy_deployments_are_excluded(registry, resolver, health) -> None:
    for _ in range(3):
        health.record_failure("mock-small-local", "provider_unavailable")

    result = resolver.resolve(registry, request_for())

    assert health.state_for("mock-small-local") is HealthState.OFFLINE
    assert [candidate.deployment.key for candidate in result.candidates] == [
        "mock-reasoning-private"
    ]


def test_warming_deployments_are_excluded(registry, resolver, health) -> None:
    health.record_probe("mock-small-local", HealthState.WARMING, None, "loading weights")

    result = resolver.resolve(registry, request_for())

    assert [candidate.deployment.key for candidate in result.candidates] == [
        "mock-reasoning-private"
    ]


def test_degraded_deployments_are_deprioritized_not_excluded(registry, resolver, health) -> None:
    health.record_failure("mock-small-local", "provider_unavailable")

    result = resolver.resolve(registry, request_for())

    assert health.state_for("mock-small-local") is HealthState.DEGRADED
    keys = [candidate.deployment.key for candidate in result.candidates]
    assert keys == ["mock-reasoning-private", "mock-small-local"]


def test_explanation_is_safe_and_names_the_model(registry, resolver) -> None:
    result = resolver.resolve(registry, request_for(mode=ExecutionMode.PRIVATE))

    assert "Janus Mock" in result.explanation
    # Never internal endpoints, credentials, or model reasoning.
    assert "http" not in result.explanation
    assert "token" not in result.explanation.lower()


def test_no_eligible_model_error_carries_a_hint_without_leaking(registry, resolver) -> None:
    with pytest.raises(PolicyViolationError) as excinfo:
        resolver.resolve(
            registry, request_for(constraints=RoutingConstraints(regions=["eu-central-1"]))
        )

    details = excinfo.value.details
    assert "hint" in details
    assert "excluded_counts" in details
    # Aggregate counts only: the caller does not learn which deployments exist.
    assert "mock-reasoning-private" not in str(details)
