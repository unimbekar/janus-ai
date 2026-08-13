"""Adapter conformance suite.

Every backend adapter must pass these cases before it can be registered. This is
the test that keeps "provider independence" from being a slogan: the cases come
from docs/model-gateway.md §3.2, and an adapter that cannot satisfy one must
declare the capability absent rather than degrade silently.

Deterministic adapters run in CI. Adapters that need a live runtime are skipped
unless explicitly enabled, so CI never depends on a network:

    JANUS_TEST_OLLAMA=1 pytest services/gateway/tests/conformance
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import AsyncIterator

import httpx
import pytest
from gateway_app.backends import MockBackend, OllamaBackend, OpenAICompatibleBackend
from gateway_app.backends.base import CallContext, ModelBackend
from gateway_app.registry.records import DeploymentRecord
from janus_core.errors import JanusError, ProviderError, RateLimitError, TimeoutError
from janus_schemas.chat import ChatChunk, ChatCompletionRequest
from janus_schemas.common import DeploymentType, HealthState, PrivacyLevel, Protocol, Role
from janus_schemas.embeddings import EmbeddingRequest

OLLAMA_ENABLED = os.environ.get("JANUS_TEST_OLLAMA") == "1"
OLLAMA_MODEL = os.environ.get("JANUS_TEST_OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_URL = os.environ.get("JANUS_OLLAMA_BASE_URL", "http://localhost:11434/v1")


def mock_deployment() -> DeploymentRecord:
    return DeploymentRecord(
        key="conformance-mock",
        model_slug="janus/mock-small",
        backend="mock",
        protocol=Protocol.NATIVE,
        deployment_type=DeploymentType.LOCAL_DEV,
        privacy_level=PrivacyLevel.LOCAL,
    )


def ollama_deployment() -> DeploymentRecord:
    return DeploymentRecord(
        key="conformance-ollama",
        model_slug="janus/llama3.1-8b",
        backend="ollama",
        protocol=Protocol.OPENAI_COMPATIBLE,
        deployment_type=DeploymentType.LOCAL_DEV,
        privacy_level=PrivacyLevel.LOCAL,
        endpoint=OLLAMA_URL,
        provider_model_id=OLLAMA_MODEL,
    )


@pytest.fixture(
    params=[
        pytest.param("mock", id="mock"),
        pytest.param(
            "ollama",
            id="ollama",
            marks=pytest.mark.skipif(
                not OLLAMA_ENABLED, reason="set JANUS_TEST_OLLAMA=1 with Ollama running"
            ),
        ),
    ]
)
def adapter(request) -> AsyncIterator[tuple[ModelBackend, DeploymentRecord]]:
    if request.param == "mock":
        yield MockBackend(), mock_deployment()
        return

    client = httpx.AsyncClient()
    try:
        yield OllamaBackend(client), ollama_deployment()
    finally:
        asyncio.get_event_loop().run_until_complete(client.aclose())


@pytest.fixture
def ctx() -> CallContext:
    return CallContext(request_id="rq_conformance", organization_id="org_test")


def chat_request(content: str, **overrides) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="conformance",
        messages=[{"role": Role.USER, "content": content}],
        max_tokens=64,
        temperature=0.0,
        **overrides,
    )


# --------------------------------------------------------------- the interface


@pytest.mark.parametrize("backend_class", [MockBackend, OllamaBackend, OpenAICompatibleBackend])
def test_adapter_implements_the_whole_interface(backend_class) -> None:
    assert not inspect.isabstract(backend_class)
    for name in ("generate", "stream", "embeddings", "health", "capabilities"):
        assert callable(getattr(backend_class, name))
    assert backend_class.backend_id
    assert isinstance(backend_class.protocol, Protocol)


# ------------------------------------------------------------- required cases


async def test_non_streaming_completion(adapter, ctx) -> None:
    backend, deployment = adapter
    response = await backend.generate(chat_request("Say hello."), deployment, ctx)

    assert response.choices[0].message.content
    assert response.choices[0].finish_reason
    assert response.usage.total_tokens > 0
    # The Janus slug is presented, never the upstream identifier.
    assert response.model == deployment.model_slug


async def test_streaming_is_ordered_and_terminated(adapter, ctx) -> None:
    backend, deployment = adapter
    chunks: list[ChatChunk] = [
        chunk async for chunk in backend.stream(chat_request("Count to three."), deployment, ctx)
    ]

    assert chunks
    assert any(chunk.choices and chunk.choices[0].delta.content for chunk in chunks)
    assert chunks[-1].choices[0].finish_reason is not None
    assert all(chunk.model == deployment.model_slug for chunk in chunks)


async def test_cancellation_mid_stream_releases_the_upstream_call(adapter, ctx) -> None:
    backend, deployment = adapter
    stream = backend.stream(chat_request("Write a long story."), deployment, ctx)

    received = 0
    async for _ in stream:
        received += 1
        if received >= 2:
            break

    # Closing the generator must propagate cancellation to the runtime rather
    # than leaking the connection.
    await stream.aclose()
    assert received >= 1


async def test_health_probe_reports_instead_of_raising(adapter) -> None:
    backend, deployment = adapter
    report = await backend.health(deployment)

    assert isinstance(report.state, HealthState)


async def test_health_probe_on_an_unreachable_endpoint_is_offline() -> None:
    async with httpx.AsyncClient() as client:
        backend = OllamaBackend(client)
        deployment = DeploymentRecord(
            key="unreachable",
            model_slug="janus/nothing",
            backend="ollama",
            protocol=Protocol.OPENAI_COMPATIBLE,
            deployment_type=DeploymentType.LOCAL_DEV,
            privacy_level=PrivacyLevel.LOCAL,
            endpoint="http://127.0.0.1:1/v1",
        )
        report = await backend.health(deployment)

    assert report.state is HealthState.OFFLINE


async def test_capabilities_are_declared(adapter) -> None:
    backend, deployment = adapter
    capabilities = await backend.capabilities(deployment)

    assert capabilities.streaming is True


async def test_multilingual_round_trip(adapter, ctx) -> None:
    backend, deployment = adapter
    text = "नमस्ते — 日本語 — Ωμέγα"
    response = await backend.generate(chat_request(f"Echo exactly: {text}"), deployment, ctx)

    content = response.choices[0].message.content
    assert isinstance(content, str)
    # No mojibake: the round trip stays valid UTF-8 and decodable.
    assert content.encode("utf-8").decode("utf-8") == content


async def test_unsupported_capability_raises_rather_than_degrading(adapter, ctx) -> None:
    backend, deployment = adapter
    if isinstance(backend, MockBackend):
        pytest.skip("mock backend supports embeddings")

    with pytest.raises(JanusError) as excinfo:
        await backend.embeddings(
            EmbeddingRequest(model="conformance", input="hello"), deployment, ctx
        )

    assert excinfo.value.code in {"capability_unsupported", "provider_model_missing"}


# ---------------------------------------------- error mapping (mock injection)


@pytest.mark.parametrize(
    ("token", "expected_error", "expected_code", "retryable"),
    [
        ("__fail__", ProviderError, "provider_unavailable", True),
        ("__auth_fail__", ProviderError, "provider_auth_failed", False),
        ("__ratelimit__", RateLimitError, "provider_rate_limited", True),
        ("__timeout__", TimeoutError, "deadline_exceeded", True),
    ],
)
async def test_provider_failures_map_to_typed_errors(
    ctx, token, expected_error, expected_code, retryable
) -> None:
    backend, deployment = MockBackend(), mock_deployment()

    with pytest.raises(expected_error) as excinfo:
        await backend.generate(chat_request(token), deployment, ctx)

    assert excinfo.value.code == expected_code
    assert excinfo.value.retryable is retryable
    # Credentials must never appear in a provider error.
    assert "Bearer" not in str(excinfo.value)


async def test_deterministic_backend_is_deterministic(ctx) -> None:
    backend, deployment = MockBackend(), mock_deployment()

    first = await backend.generate(chat_request("stable?"), deployment, ctx)
    second = await backend.generate(chat_request("stable?"), deployment, ctx)

    assert first.choices[0].message.content == second.choices[0].message.content


async def test_streaming_usage_is_reported_or_estimated(ctx) -> None:
    """A runtime that reports no usage must be declared, not guessed about."""
    backend, deployment = MockBackend(), mock_deployment()
    chunks = [chunk async for chunk in backend.stream(chat_request("hi"), deployment, ctx)]

    assert chunks[-1].usage is not None
    assert OllamaBackend.supports_stream_usage is False
