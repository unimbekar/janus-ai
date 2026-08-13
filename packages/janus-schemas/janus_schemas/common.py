"""Enumerations shared across the platform.

These mirror the PostgreSQL enums in docs/database.md. Changing a value here
requires a migration, so treat them as schema.
"""

from __future__ import annotations

from enum import StrEnum


class ExecutionMode(StrEnum):
    """Where inference is permitted to happen (docs/architecture.md §8)."""

    AUTO = "auto"
    CLOUD = "cloud"
    PRIVATE = "private"
    SOVEREIGN = "sovereign"
    OFFLINE = "offline"

    @property
    def allows_external_providers(self) -> bool:
        return self in (ExecutionMode.AUTO, ExecutionMode.CLOUD)

    @property
    def allows_janus_hosted(self) -> bool:
        return self is not ExecutionMode.OFFLINE

    @property
    def allows_local(self) -> bool:
        return self in (
            ExecutionMode.AUTO,
            ExecutionMode.PRIVATE,
            ExecutionMode.OFFLINE,
        )

    @property
    def restrictiveness(self) -> int:
        """Higher is more restrictive. Used for most-restrictive-wins resolution."""
        return _MODE_RESTRICTIVENESS[self]


_MODE_RESTRICTIVENESS: dict[ExecutionMode, int] = {
    ExecutionMode.AUTO: 0,
    ExecutionMode.CLOUD: 1,
    ExecutionMode.PRIVATE: 2,
    ExecutionMode.SOVEREIGN: 3,
    ExecutionMode.OFFLINE: 4,
}


class Classification(StrEnum):
    """Data sensitivity (docs/security.md §5). Highest present in context wins."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"

    @property
    def rank(self) -> int:
        return _CLASSIFICATION_RANK[self]


_CLASSIFICATION_RANK: dict[Classification, int] = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelType(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    TRANSCRIPTION = "transcription"
    SPEECH = "speech"
    IMAGE = "image"


class ModelTier(StrEnum):
    RECOMMENDED = "recommended"
    FRONTIER = "frontier"
    OPEN_SOURCE = "open_source"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"


class DeploymentType(StrEnum):
    PROVIDER_CLOUD = "provider_cloud"
    JANUS_GPU = "janus_gpu"
    JANUS_CPU = "janus_cpu"
    LOCAL_DEV = "local_dev"
    CUSTOMER_VPC = "customer_vpc"


class PrivacyLevel(StrEnum):
    PROVIDER = "provider"
    PRIVATE = "private"
    LOCAL = "local"


class HealthState(StrEnum):
    PROVISIONING = "provisioning"
    WARMING = "warming"
    READY = "ready"
    OVERLOADED = "overloaded"
    DEGRADED = "degraded"
    DRAINING = "draining"
    OFFLINE = "offline"

    @property
    def is_routable(self) -> bool:
        """Whether the router may send production traffic here.

        ``overloaded`` and ``degraded`` remain routable but are deprioritized by
        scoring; warming, draining, provisioning, and offline never are.
        """
        return self in (HealthState.READY, HealthState.OVERLOADED, HealthState.DEGRADED)


class Protocol(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    NATIVE = "native"


class CostClass(StrEnum):
    FREE = "free"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    FIXED = "fixed"


class LatencyClass(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
