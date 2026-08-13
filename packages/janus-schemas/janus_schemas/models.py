"""Model catalog contracts returned by ``/v1/models`` and ``/v1/providers``.

Two deliberate omissions, both security-relevant: deployment ``endpoint`` values
and provider credential references never appear in these models, so they cannot
leak by accident through a response.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from janus_schemas.common import (
    CostClass,
    DeploymentType,
    LatencyClass,
    ModelTier,
    ModelType,
    PrivacyLevel,
)


class ModelCapabilities(BaseModel):
    """Declared capabilities. Verified by conformance tests and evaluations."""

    model_config = ConfigDict(extra="forbid")

    reasoning: bool = False
    agentic: bool = False
    tool_calling: bool = False
    structured_output: bool = False
    long_context: bool = False
    coding: bool = False
    multilingual: bool = False
    indic: bool = False
    vision: bool = False
    audio: bool = False
    documents: bool = False
    embeddings: bool = False
    streaming: bool = True
    privacy: bool = False

    def satisfies(self, required: list[str]) -> bool:
        """Whether every requested capability is present."""
        return all(getattr(self, name, False) for name in required)

    def missing(self, required: list[str]) -> list[str]:
        return [name for name in required if not getattr(self, name, False)]


class DeploymentSummary(BaseModel):
    """Public view of a deployment: no endpoint, no credentials."""

    model_config = ConfigDict(extra="forbid")

    key: str
    type: DeploymentType
    privacy: PrivacyLevel
    region: str | None = None
    availability: str = "ready"
    max_context: int | None = None


class LicenseSummary(BaseModel):
    name: str
    attribution_text: str | None = None
    commercial_use: bool | None = None


class ModelJanusMetadata(BaseModel):
    """The ``janus`` block on a model list entry."""

    display_name: str
    tier: ModelTier
    type: ModelType
    context_window: int
    max_output_tokens: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])
    deployments: list[DeploymentSummary] = Field(default_factory=list)
    cost_class: CostClass = CostClass.MEDIUM
    latency_class: LatencyClass = LatencyClass.MEDIUM
    license: LicenseSummary | None = None
    metadata_verified: bool = False
    notes: str | None = None


class ModelInfo(BaseModel):
    """OpenAI-shaped model entry with the Janus extension attached."""

    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str
    janus: ModelJanusMetadata


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelInfo] = Field(default_factory=list)


class ProviderInfo(BaseModel):
    id: str
    display_name: str
    kind: Literal["cloud", "janus_hosted", "local"]
    status: str = "active"
    model_count: int = 0


class ProviderList(BaseModel):
    object: Literal["list"] = "list"
    data: list[ProviderInfo] = Field(default_factory=list)
