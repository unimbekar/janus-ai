"""Deterministic requirement inference."""

from __future__ import annotations

from gateway_app.router.infer import infer_requirements, merge_requirements
from gateway_app.router.resolver import ResolutionRequest
from janus_schemas.chat import ChatMessage, RoutingRequirements
from janus_schemas.common import Role


def test_code_fences_request_coding() -> None:
    inferred = infer_requirements(
        [ChatMessage(role=Role.USER, content="Fix this:\n```python\nprint(1)\n```")]
    )
    assert "coding" in inferred.capabilities


def test_devanagari_requests_hindi_and_indic() -> None:
    inferred = infer_requirements([ChatMessage(role=Role.USER, content="इस अनुबंध का सारांश दें।")])
    assert "hi" in inferred.languages
    assert "indic" in inferred.capabilities
    assert "multilingual" in inferred.capabilities


def test_explicit_requirements_are_kept_and_inference_fills_gaps() -> None:
    explicit = RoutingRequirements(capabilities=["reasoning"], min_context=8000)
    inferred = RoutingRequirements(capabilities=["coding"], languages=["hi"])
    merged = merge_requirements(explicit, inferred)
    assert merged.capabilities == ["reasoning", "coding"]
    assert merged.languages == ["hi"]
    assert merged.min_context == 8000


def test_inferred_languages_do_not_exclude_an_explicit_model(registry, resolver) -> None:
    """Inference ranks; it does not veto a model the caller named."""
    inferred = infer_requirements(
        [ChatMessage(role=Role.USER, content="この契約を要約してください。")]
    )
    result = resolver.resolve(
        registry,
        ResolutionRequest(
            model="janus/mock-small",
            requirements=RoutingRequirements(),
            preferences=inferred,
        ),
    )
    assert result.primary.model.slug == "janus/mock-small"
