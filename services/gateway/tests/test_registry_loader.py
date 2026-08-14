"""Registry loader behavior.

The loader is a security-relevant component: it decides what exists, where it
runs, and how private it is. Unusable input must fail loudly at startup rather
than produce a catalog that is subtly wrong.
"""

from __future__ import annotations

import pytest
from gateway_app.registry.loader import RegistryLoadError, load_registry
from janus_schemas.common import HealthState, PrivacyLevel


def test_loads_test_environment(settings) -> None:
    registry = load_registry(settings.registry_dir, "test")

    slugs = {model.slug for model in registry.models}
    assert {"janus/mock-small", "janus/mock-reasoning"} <= slugs
    # Cloud adapters are catalogued as YAML, but their deployments are absent
    # from the test overlay so no test can reach a provider.
    assert "openai/gpt-4o" in slugs
    assert not registry.deployments_by_key.get("openai-gpt4o-us")

    # The Ollama deployment is absent from the test overlay, so it does not
    # exist here even though the model file declares it.
    keys = set(registry.deployments_by_key)
    assert "ollama-llama31-8b" not in keys
    assert {"mock-small-local", "mock-reasoning-private"} <= keys


def test_environment_overlay_changes_what_exists(settings) -> None:
    test_env = load_registry(settings.registry_dir, "test")
    local_env = load_registry(settings.registry_dir, "local")
    prod_env = load_registry(settings.registry_dir, "prod")

    assert "ollama-llama31-8b" in local_env.deployments_by_key
    assert "ollama-llama31-8b" not in test_env.deployments_by_key
    # Production has no reviewed provider deployment yet, so nothing is servable.
    assert prod_env.servable_models() == []


def test_endpoint_environment_expansion(settings, monkeypatch) -> None:
    monkeypatch.setenv("JANUS_OLLAMA_BASE_URL", "http://ollama.internal:11434/v1")
    registry = load_registry(settings.registry_dir, "local")

    _, deployment = registry.deployments_by_key["ollama-llama31-8b"]
    assert deployment.endpoint == "http://ollama.internal:11434/v1"


def test_endpoint_expansion_falls_back_to_default(settings, monkeypatch) -> None:
    monkeypatch.delenv("JANUS_OLLAMA_BASE_URL", raising=False)
    registry = load_registry(settings.registry_dir, "local")

    _, deployment = registry.deployments_by_key["ollama-llama31-8b"]
    assert deployment.endpoint == "http://localhost:11434/v1"


def test_declared_privacy_and_health_are_preserved(settings) -> None:
    registry = load_registry(settings.registry_dir, "local")

    _, private = registry.deployments_by_key["mock-reasoning-private"]
    assert private.privacy_level is PrivacyLevel.PRIVATE
    assert private.region == "us-east-1"

    _, ollama = registry.deployments_by_key["ollama-llama31-8b"]
    assert ollama.privacy_level is PrivacyLevel.LOCAL
    assert ollama.initial_health is HealthState.WARMING


def test_unknown_capability_is_rejected(tmp_path) -> None:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "bad.yaml").write_text(
        """
slug: bad/model
display_name: Bad Model
provider: test
context_window: 1024
capabilities: [reasoning, telepathy]
""",
        encoding="utf-8",
    )

    with pytest.raises(RegistryLoadError, match="telepathy"):
        load_registry(tmp_path, "test")


def test_duplicate_deployment_keys_are_rejected(tmp_path) -> None:
    (tmp_path / "models").mkdir()
    for index in (1, 2):
        (tmp_path / "models" / f"model{index}.yaml").write_text(
            f"""
slug: test/model{index}
display_name: Model {index}
provider: test
context_window: 1024
deployments:
  - key: shared-key
    backend: mock
    deployment_type: local_dev
    privacy_level: local
""",
            encoding="utf-8",
        )

    with pytest.raises(RegistryLoadError, match="duplicate deployment keys"):
        load_registry(tmp_path, "test")


def test_missing_required_field_is_rejected(tmp_path) -> None:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "incomplete.yaml").write_text(
        """
slug: test/incomplete
display_name: Incomplete
provider: test
deployments: []
""",
        encoding="utf-8",
    )

    with pytest.raises(RegistryLoadError, match="context_window"):
        load_registry(tmp_path, "test")


def test_missing_models_directory_is_fatal(tmp_path) -> None:
    with pytest.raises(RegistryLoadError):
        load_registry(tmp_path / "nope", "test")
