"""Model registry: the catalog of models and their deployments."""

from gateway_app.registry.loader import RegistryLoadError, load_registry
from gateway_app.registry.records import DeploymentRecord, ModelRecord, Registry

__all__ = [
    "DeploymentRecord",
    "ModelRecord",
    "Registry",
    "RegistryLoadError",
    "load_registry",
]
