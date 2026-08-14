"""Internal registry records.

These are the *internal* representations, and they carry material that must not
reach a public response: deployment endpoints and credential references. The
public projection lives in ``janus_schemas.models``, and the split is what makes
leaking an endpoint a type error rather than an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
from janus_schemas.models import (
    DeploymentSummary,
    LicenseSummary,
    ModelCapabilities,
    ModelInfo,
    ModelJanusMetadata,
)


@dataclass(frozen=True, slots=True)
class DeploymentRecord:
    """One servable instance of a model."""

    key: str
    model_slug: str
    backend: str
    protocol: Protocol
    deployment_type: DeploymentType
    privacy_level: PrivacyLevel

    endpoint: str | None = None
    credentials_ref: str | None = None
    provider_model_id: str | None = None
    region: str | None = None
    data_residency: tuple[str, ...] = ()
    max_context: int | None = None
    max_concurrency: int | None = None
    capability_overrides: dict[str, bool] = field(default_factory=dict)
    hardware: dict[str, str | int] = field(default_factory=dict)
    priority: int = 100
    enabled: bool = True
    initial_health: HealthState = HealthState.READY

    @property
    def ref(self) -> str:
        """Deployment-qualified reference, e.g. ``janus/llama-70b@janus-gpu-use1``."""
        return f"{self.model_slug}@{self.key}"

    @property
    def upstream_model_id(self) -> str:
        """The identifier the upstream runtime expects."""
        return self.provider_model_id or self.model_slug

    def to_summary(self, availability: HealthState) -> DeploymentSummary:
        return DeploymentSummary(
            key=self.key,
            type=self.deployment_type,
            privacy=self.privacy_level,
            region=self.region,
            availability=availability.value,
            max_context=self.max_context,
            accelerator=str(self.hardware.get("accelerator")) if self.hardware else None,
        )


@dataclass(frozen=True, slots=True)
class ModelRecord:
    """A model, independent of where it runs."""

    slug: str
    display_name: str
    provider: str
    type: ModelType
    context_window: int
    capabilities: ModelCapabilities
    tier: ModelTier = ModelTier.RECOMMENDED
    family: str | None = None
    version: str | None = None
    parameters: str | None = None
    max_output_tokens: int | None = None
    languages: tuple[str, ...] = ()
    input_modalities: tuple[str, ...] = ("text",)
    output_modalities: tuple[str, ...] = ("text",)
    cost_class: CostClass = CostClass.MEDIUM
    latency_class: LatencyClass = LatencyClass.MEDIUM
    status: str = "active"
    license: LicenseSummary | None = None
    aliases: tuple[str, ...] = ()
    metadata_verified: bool = False
    notes: str | None = None
    deployments: tuple[DeploymentRecord, ...] = ()

    def capabilities_for(self, deployment: DeploymentRecord) -> ModelCapabilities:
        """Model capabilities with deployment reality applied.

        Deployment overrides always win: a runtime that cannot do structured
        output makes the model unable to do it *there*, whatever the model
        record claims.
        """
        if not deployment.capability_overrides:
            return self.capabilities
        return self.capabilities.model_copy(update=deployment.capability_overrides)

    def enabled_capability_names(self) -> list[str]:
        return [name for name, value in self.capabilities.model_dump().items() if value]

    def to_public(self, availability: dict[str, HealthState]) -> ModelInfo:
        return ModelInfo(
            id=self.slug,
            owned_by=self.provider,
            janus=ModelJanusMetadata(
                display_name=self.display_name,
                tier=self.tier,
                type=self.type,
                context_window=self.context_window,
                max_output_tokens=self.max_output_tokens,
                capabilities=self.enabled_capability_names(),
                languages=list(self.languages),
                input_modalities=list(self.input_modalities),
                output_modalities=list(self.output_modalities),
                deployments=[
                    deployment.to_summary(availability.get(deployment.key, HealthState.READY))
                    for deployment in self.deployments
                ],
                cost_class=self.cost_class,
                latency_class=self.latency_class,
                license=self.license,
                metadata_verified=self.metadata_verified,
                notes=self.notes,
            ),
        )


@dataclass(frozen=True, slots=True)
class Registry:
    """Immutable, indexed snapshot of the catalog.

    Immutability is intentional: a reload swaps the whole snapshot, so a request
    can never observe a half-applied catalog change.
    """

    models: tuple[ModelRecord, ...]
    environment: str

    @property
    def by_slug(self) -> dict[str, ModelRecord]:
        return {model.slug: model for model in self.models}

    @property
    def deployments_by_key(self) -> dict[str, tuple[ModelRecord, DeploymentRecord]]:
        return {
            deployment.key: (model, deployment)
            for model in self.models
            for deployment in model.deployments
        }

    def get_model(self, slug: str) -> ModelRecord | None:
        return self.by_slug.get(slug)

    def resolve_alias(self, alias: str) -> list[ModelRecord]:
        """Models registered under a ``janus/<class>`` alias, best first."""
        return [model for model in self.models if alias in model.aliases]

    def servable_models(self) -> list[ModelRecord]:
        return [
            model
            for model in self.models
            if model.status == "active" and any(d.enabled for d in model.deployments)
        ]
