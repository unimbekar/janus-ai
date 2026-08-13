"""Chat completion contracts: OpenAI-compatible surface plus the ``janus`` block.

Compatibility rule from ADR 0002: an unmodified OpenAI client must work when it
ignores the ``janus`` field, and Janus must never require the extension for a
basic request.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from janus_schemas.common import Classification, ExecutionMode, Role


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Role
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class RoutingRequirements(BaseModel):
    """What the request needs. Requirements filter; they do not rank."""

    capabilities: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    min_context: int | None = Field(default=None, ge=1)


class RoutingConstraints(BaseModel):
    """Hard limits. A candidate that violates one is excluded, never downgraded."""

    max_cost_usd: float | None = Field(default=None, gt=0)
    max_latency_ms: int | None = Field(default=None, gt=0)
    regions: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    exclude_providers: list[str] = Field(default_factory=list)


class RoutingOptions(BaseModel):
    explain: bool = False
    allow_fallback: bool = True


class JanusRequestOptions(BaseModel):
    """The ``janus`` request extension (docs/api.md §3.1)."""

    model_config = ConfigDict(extra="forbid")

    mode: ExecutionMode | None = None
    classification: Classification | None = None
    requirements: RoutingRequirements = Field(default_factory=RoutingRequirements)
    constraints: RoutingConstraints = Field(default_factory=RoutingConstraints)
    routing: RoutingOptions = Field(default_factory=RoutingOptions)
    conversation_id: str | None = None
    agent_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(description="'auto', a janus/ alias, a slug, or slug@deployment")
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, gt=0)
    stop: str | list[str] | None = None
    seed: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    user: str | None = None
    janus: JanusRequestOptions = Field(default_factory=JanusRequestOptions)

    @field_validator("messages")
    @classmethod
    def _require_non_system_message(cls, value: list[ChatMessage]) -> list[ChatMessage]:
        if all(message.role is Role.SYSTEM for message in value):
            raise ValueError("at least one non-system message is required")
        return value


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class JanusResponseMetadata(BaseModel):
    """What actually served the request. Internal endpoints are never included."""

    request_id: str
    model: str
    deployment: str
    provider: str
    privacy: str
    region: str | None = None
    mode: ExecutionMode
    fallback_used: bool = False
    capability_downgraded: list[str] = Field(default_factory=list)
    routing_reason: str | None = None
    routing_explanation: str | None = None
    cost_usd: float | None = None
    ttft_ms: int | None = None
    total_ms: int | None = None


class ChatChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoice]
    usage: Usage = Field(default_factory=Usage)
    janus: JanusResponseMetadata | None = None


class ChatDelta(BaseModel):
    role: Role | None = None
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatChunkChoice(BaseModel):
    index: int = 0
    delta: ChatDelta = Field(default_factory=ChatDelta)
    finish_reason: str | None = None


class ChatChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatChunkChoice]
    usage: Usage | None = None


class JanusRoutingEvent(BaseModel):
    """``event: janus.routing`` — emitted before the first content chunk."""

    request_id: str
    model: str
    deployment: str
    provider: str
    privacy: str
    fallback_used: bool = False
    routing_explanation: str | None = None


class JanusUsageEvent(BaseModel):
    """``event: janus.usage`` — emitted after the final content chunk."""

    request_id: str
    usage: Usage
    cost_usd: float | None = None
    ttft_ms: int | None = None
    total_ms: int | None = None
