"""Load the registry from YAML (registry-as-code).

Layout:

    registry/models/<slug>.yaml           canonical model + all known deployments
    registry/environments/<env>.yaml      which deployments exist in this environment

A model change is therefore a reviewed pull request, not a console click, and
the same files seed the database in later phases.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from janus_core.logging import get_logger
from janus_schemas.common import (
    CostClass,
    DeploymentType,
    HealthState,
    LatencyClass,
    ModelTier,
    ModelType,
    PrivacyLevel,
    Protocol,
)
from janus_schemas.models import LicenseSummary, ModelCapabilities

from gateway_app.registry.records import DeploymentRecord, ModelRecord, Registry

logger = get_logger(__name__)

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Z0-9_]+)(?::-(?P<default>[^}]*))?\}")


class RegistryLoadError(Exception):
    """Raised when the catalog on disk is unusable.

    Always fatal at startup: serving with a partially understood catalog risks
    routing to a deployment whose privacy or region is wrong.
    """


def _expand(value: Any) -> Any:
    """Substitute ``${VAR}`` / ``${VAR:-default}`` in strings, recursively."""
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group("name")
            default = match.group("default")
            resolved = os.environ.get(name, default)
            if resolved is None:
                raise RegistryLoadError(f"environment variable {name} is required by the registry")
            return resolved

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    return value


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RegistryLoadError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise RegistryLoadError(f"{path}: expected a mapping at the top level")
    return _expand(raw)


def _capabilities(names: list[str] | None, path: Path) -> ModelCapabilities:
    if not names:
        return ModelCapabilities()
    known = set(ModelCapabilities.model_fields)
    unknown = [name for name in names if name not in known]
    if unknown:
        raise RegistryLoadError(f"{path}: unknown capabilities {unknown}")
    return ModelCapabilities(**dict.fromkeys(names, True))


def _deployment(raw: dict[str, Any], model_slug: str, path: Path) -> DeploymentRecord:
    try:
        return DeploymentRecord(
            key=raw["key"],
            model_slug=model_slug,
            backend=raw["backend"],
            protocol=Protocol(raw.get("protocol", Protocol.OPENAI_COMPATIBLE)),
            deployment_type=DeploymentType(raw["deployment_type"]),
            privacy_level=PrivacyLevel(raw["privacy_level"]),
            endpoint=raw.get("endpoint"),
            credentials_ref=raw.get("credentials_ref"),
            provider_model_id=raw.get("provider_model_id"),
            region=raw.get("region"),
            data_residency=tuple(raw.get("data_residency", ())),
            max_context=raw.get("max_context"),
            max_concurrency=raw.get("max_concurrency"),
            capability_overrides=dict(raw.get("capability_overrides", {})),
            hardware={str(key): value for key, value in dict(raw.get("hardware") or {}).items()},
            priority=int(raw.get("priority", 100)),
            initial_health=HealthState(raw.get("initial_health", HealthState.READY)),
        )
    except KeyError as exc:
        raise RegistryLoadError(f"{path}: deployment missing required field {exc}") from exc
    except ValueError as exc:
        raise RegistryLoadError(f"{path}: invalid deployment value: {exc}") from exc


def _model(raw: dict[str, Any], path: Path, enabled_keys: set[str] | None) -> ModelRecord:
    try:
        slug = raw["slug"]
        deployments = [_deployment(item, slug, path) for item in raw.get("deployments", [])]
        if enabled_keys is not None:
            deployments = [d for d in deployments if d.key in enabled_keys]

        license_raw = raw.get("license")
        license_summary = (
            LicenseSummary(**license_raw)
            if isinstance(license_raw, dict)
            else LicenseSummary(name=license_raw)
            if isinstance(license_raw, str)
            else None
        )

        return ModelRecord(
            slug=slug,
            display_name=raw["display_name"],
            provider=raw["provider"],
            type=ModelType(raw.get("type", ModelType.CHAT)),
            context_window=int(raw["context_window"]),
            capabilities=_capabilities(raw.get("capabilities"), path),
            tier=ModelTier(raw.get("tier", ModelTier.RECOMMENDED)),
            family=raw.get("family"),
            version=raw.get("version"),
            parameters=raw.get("parameters"),
            max_output_tokens=raw.get("max_output_tokens"),
            languages=tuple(raw.get("languages", ())),
            input_modalities=tuple(raw.get("input_modalities", ("text",))),
            output_modalities=tuple(raw.get("output_modalities", ("text",))),
            cost_class=CostClass(raw.get("cost_class", CostClass.MEDIUM)),
            latency_class=LatencyClass(raw.get("latency_class", LatencyClass.MEDIUM)),
            status=raw.get("status", "active"),
            license=license_summary,
            aliases=tuple(raw.get("aliases", ())),
            metadata_verified=bool(raw.get("metadata_verified", False)),
            notes=raw.get("notes"),
            deployments=tuple(sorted(deployments, key=lambda d: d.priority)),
        )
    except KeyError as exc:
        raise RegistryLoadError(f"{path}: model missing required field {exc}") from exc
    except ValueError as exc:
        raise RegistryLoadError(f"{path}: invalid model value: {exc}") from exc


def load_registry(registry_dir: Path, environment: str) -> Registry:
    """Build a catalog snapshot for one environment."""
    models_dir = registry_dir / "models"
    if not models_dir.is_dir():
        raise RegistryLoadError(f"{models_dir} does not exist")

    overlay_path = registry_dir / "environments" / f"{environment}.yaml"
    enabled_keys: set[str] | None = None
    if overlay_path.is_file():
        overlay = _read_yaml(overlay_path)
        listed = overlay.get("enabled_deployments")
        if listed is not None:
            enabled_keys = set(listed)
    else:
        logger.warning(
            "registry_environment_overlay_missing",
            extra={"path": str(overlay_path), "effect": "all deployments enabled"},
        )

    models: list[ModelRecord] = []
    for path in sorted(models_dir.glob("*.yaml")):
        models.append(_model(_read_yaml(path), path, enabled_keys))

    slugs = [model.slug for model in models]
    duplicates = {slug for slug in slugs if slugs.count(slug) > 1}
    if duplicates:
        raise RegistryLoadError(f"duplicate model slugs: {sorted(duplicates)}")

    keys = [d.key for model in models for d in model.deployments]
    duplicate_keys = {key for key in keys if keys.count(key) > 1}
    if duplicate_keys:
        raise RegistryLoadError(f"duplicate deployment keys: {sorted(duplicate_keys)}")

    registry = Registry(models=tuple(models), environment=environment)
    logger.info(
        "registry_loaded",
        extra={
            "environment": environment,
            "model_count": len(models),
            "deployment_count": len(keys),
        },
    )
    return registry
