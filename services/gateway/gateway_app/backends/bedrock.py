"""Amazon Bedrock Converse API adapter."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from janus_core.errors import ProviderError
from janus_schemas.chat import (
    ChatChoice,
    ChatChunk,
    ChatChunkChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatDelta,
    ChatMessage,
    Usage,
)
from janus_schemas.common import HealthState, Protocol, Role
from janus_schemas.embeddings import EmbeddingRequest, EmbeddingResponse
from janus_schemas.models import ModelCapabilities

from gateway_app.backends.base import (
    CallContext,
    HealthReport,
    ModelBackend,
    UnsupportedCapabilityError,
)
from gateway_app.registry.records import DeploymentRecord


class BedrockBackend(ModelBackend):
    backend_id = "bedrock"
    protocol = Protocol.NATIVE

    def __init__(self) -> None:
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise ProviderError("boto3 is required for Bedrock.") from exc
            self._client = boto3.client("bedrock-runtime")
        return self._client

    def _model_id(self, deployment: DeploymentRecord) -> str:
        return deployment.provider_model_id or deployment.upstream_model_id

    def _messages(self, request: ChatCompletionRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "system":
                continue
            role = "assistant" if message.role == "assistant" else "user"
            content = message.content if isinstance(message.content, str) else str(message.content)
            out.append({"role": role, "content": [{"text": content}]})
        return out

    async def generate(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> ChatCompletionResponse:
        body = {
            "messages": self._messages(request),
            "inferenceConfig": {"maxTokens": request.max_tokens or 4096},
        }
        response = self._get_client().converse(
            modelId=self._model_id(deployment),
            messages=body["messages"],
            inferenceConfig=body["inferenceConfig"],
        )
        text = response["output"]["message"]["content"][0]["text"]
        usage_data = response.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("inputTokens", 0),
            completion_tokens=usage_data.get("outputTokens", 0),
        )
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        return ChatCompletionResponse(
            id=f"chatcmpl_bedrock_{ctx.request_id[-12:]}",
            created=int(time.time()),
            model=deployment.model_slug,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChatMessage(role=Role.ASSISTANT, content=text),
                    finish_reason="stop",
                )
            ],
            usage=usage,
        )

    async def stream(
        self,
        request: ChatCompletionRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> AsyncIterator[ChatChunk]:
        response = await self.generate(request, deployment, ctx)
        text = response.choices[0].message.content or ""
        completion_id = response.id
        created = response.created
        yield ChatChunk(
            id=completion_id,
            created=created,
            model=deployment.model_slug,
            choices=[ChatChunkChoice(index=0, delta=ChatDelta(role=Role.ASSISTANT))],
        )
        yield ChatChunk(
            id=completion_id,
            created=created,
            model=deployment.model_slug,
            choices=[ChatChunkChoice(index=0, delta=ChatDelta(content=text))],
        )
        yield ChatChunk(
            id=completion_id,
            created=created,
            model=deployment.model_slug,
            choices=[ChatChunkChoice(index=0, delta=ChatDelta(), finish_reason="stop")],
            usage=response.usage,
        )

    async def embeddings(
        self,
        request: EmbeddingRequest,
        deployment: DeploymentRecord,
        ctx: CallContext,
    ) -> EmbeddingResponse:
        raise UnsupportedCapabilityError("Bedrock embeddings use a separate model id.")

    async def health(self, deployment: DeploymentRecord) -> HealthReport:
        return HealthReport(
            state=HealthState.READY if deployment.provider_model_id else HealthState.OFFLINE
        )

    async def capabilities(self, deployment: DeploymentRecord) -> ModelCapabilities:
        return ModelCapabilities(streaming=True, reasoning=True)
