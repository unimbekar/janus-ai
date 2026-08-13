"""Registry service: holds the current snapshot and reloads it."""

from __future__ import annotations

from pathlib import Path

from janus_core.logging import get_logger

from gateway_app.registry.loader import load_registry
from gateway_app.registry.records import DeploymentRecord, ModelRecord, Registry

logger = get_logger(__name__)


class RegistryService:
    """Swaps whole snapshots so requests never see a partial catalog."""

    def __init__(self, registry_dir: Path, environment: str) -> None:
        self._registry_dir = registry_dir
        self._environment = environment
        self._registry = load_registry(registry_dir, environment)

    @property
    def current(self) -> Registry:
        return self._registry

    def reload(self) -> Registry:
        """Re-read the catalog. Keeps the old snapshot if the new one is invalid."""
        try:
            self._registry = load_registry(self._registry_dir, self._environment)
        except Exception:
            logger.exception("registry_reload_failed", extra={"effect": "kept previous snapshot"})
            raise
        return self._registry

    def find(self, slug: str) -> ModelRecord | None:
        return self._registry.get_model(slug)

    def find_deployment(self, key: str) -> tuple[ModelRecord, DeploymentRecord] | None:
        return self._registry.deployments_by_key.get(key)
