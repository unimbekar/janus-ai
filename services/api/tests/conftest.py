"""Control plane test fixtures.

These tests need a real PostgreSQL instance, because the guarantees under test —
row-level security, enums, constraints — are database behavior. SQLite would test
a different system and pass while production leaked.

    make test-api          # starts Postgres and runs these
    JANUS_TEST_DATABASE_URL=... pytest services/api/tests
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text

TEST_DATABASE_URL = os.environ.get("JANUS_TEST_DATABASE_URL")
_SKIP_NO_DATABASE = "set JANUS_TEST_DATABASE_URL (see make test-api)"

GATEWAY_TOKEN = "test-gateway-token"


def _app_database_url(owner_url: str) -> str:
    """Same database, but as the restricted role the service really uses."""
    without_scheme = owner_url.split("://", 1)[1]
    _, _, host_and_db = without_scheme.partition("@")
    return f"postgresql+asyncpg://janus_app:janus_app@{host_and_db}"


@pytest.fixture(scope="session")
def owner_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip(_SKIP_NO_DATABASE)
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def settings(owner_url, tmp_path_factory):
    from api_app.settings import ApiSettings

    return ApiSettings(
        environment="test",
        database_url=_app_database_url(owner_url),
        migration_database_url=owner_url,
        gateway_url="http://gateway.invalid",
        gateway_service_token=GATEWAY_TOKEN,
        session_cookie_secure=False,
        log_level="WARNING",
        # Argon2 at production cost would dominate the runtime of every test
        # that signs in. Never lower these outside tests.
        argon2_time_cost=1,
        argon2_memory_cost_kib=8,
        argon2_parallelism=1,
        attachment_root=str(tmp_path_factory.mktemp("attachments")),
    )


@pytest.fixture(scope="session")
def migrated_database(owner_url, settings) -> Iterator[None]:
    """Apply migrations once, from a clean schema."""
    from alembic import command
    from alembic.config import Config

    os.environ["JANUS_MIGRATION_DATABASE_URL"] = owner_url
    os.environ["JANUS_DATABASE_URL"] = settings.database_url

    config = Config("services/api/alembic.ini")
    config.set_main_option("script_location", "services/api/migrations")
    config.set_main_option("sqlalchemy.url", owner_url)

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield


@pytest_asyncio.fixture
async def db(settings, clean_tables) -> AsyncIterator[object]:
    from api_app.db import Database, create_engine

    database = Database(
        create_engine(
            settings.database_url,
            pool_size=5,
            max_overflow=0,
            statement_timeout_ms=10_000,
        )
    )
    yield database
    await database.aclose()


@pytest_asyncio.fixture
async def clean_tables(settings, migrated_database) -> AsyncIterator[None]:
    """Truncate between tests, as the owner so RLS does not hide leftovers."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.effective_migration_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE chat.citations, chat.attachments, chat.messages, "
                "chat.conversations, knowledge.chunks, knowledge.documents, "
                "knowledge.knowledge_bases, agent.checkpoints, agent.agent_steps, "
                "agent.agent_runs, agent.tools, agent.mcp_servers, "
                "agent.agent_versions, agent.agents, telemetry.usage_records, "
                "telemetry.routing_decisions, core.audit_events, core.api_keys, "
                "core.sessions, core.organization_members, core.team_members, "
                "core.teams, core.policies, core.organizations, core.users CASCADE"
            )
        )
    await engine.dispose()
    yield


DEFAULT_STREAM_FRAMES = [
    b'event: janus.routing\ndata: {"request_id":"rq_test","model":"janus/mock-small",'
    b'"deployment":"mock-small-local","provider":"janus","privacy":"local",'
    b'"fallback_used":false,"routing_explanation":"Chosen for this test."}\n\n',
    b'data: {"id":"c1","object":"chat.completion.chunk","created":0,'
    b'"model":"janus/mock-small","choices":[{"index":0,"delta":{"content":"Hello"}}]}\n\n',
    b'data: {"id":"c1","object":"chat.completion.chunk","created":0,'
    b'"model":"janus/mock-small","choices":[{"index":0,"delta":{"content":" there"},'
    b'"finish_reason":"stop"}]}\n\n',
    b'event: janus.usage\ndata: {"request_id":"rq_test",'
    b'"usage":{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10},"ttft_ms":4}\n\n',
    b"data: [DONE]\n\n",
]


@pytest.fixture
def gateway_stub():
    """A gateway that records calls instead of making them.

    The control plane must never reach a provider, so its tests must not reach a
    gateway either. What matters here is the context it forwards, and — for chat —
    the stream it gets back, which tests can script frame by frame.
    """

    class GatewayStub:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.models_response: dict = {"object": "list", "data": []}
            self.stream_frames: list[bytes] = list(DEFAULT_STREAM_FRAMES)
            #: Raised instead of streaming, to exercise the error path.
            self.stream_error: Exception | None = None
            self.completion_response: dict = {
                "id": "chatcmpl_stub",
                "object": "chat.completion",
                "created": 0,
                "model": "janus/mock-small",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "stub"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        async def list_models(self, **kwargs) -> dict:
            self.calls.append({"operation": "list_models", **kwargs})
            return self.models_response

        async def get_model(self, model_id: str, **kwargs) -> dict:
            self.calls.append({"operation": "get_model", "model_id": model_id, **kwargs})
            return {"id": model_id, "object": "model", "owned_by": "janus", "janus": {}}

        async def list_providers(self, **kwargs) -> dict:
            self.calls.append({"operation": "list_providers", **kwargs})
            return {"object": "list", "data": []}

        async def embeddings(self, payload, **kwargs) -> tuple[int, dict]:
            self.calls.append({"operation": "embeddings", "payload": payload, **kwargs})
            return 200, {"object": "list", "data": [], "model": payload.get("model", "auto")}

        async def chat_completion(self, payload, **kwargs) -> tuple[int, dict]:
            self.calls.append({"operation": "chat_completion", "payload": payload, **kwargs})
            return 200, self.completion_response

        async def stream_chat_completion(self, payload, **kwargs):
            """An async generator, exactly like the real client."""
            self.calls.append({"operation": "stream", "payload": payload, **kwargs})
            if self.stream_error is not None:
                raise self.stream_error
            for frame in self.stream_frames:
                yield frame

        async def health(self) -> bool:
            return True

        async def aclose(self) -> None:
            return None

    return GatewayStub()


@pytest.fixture
def client(settings, gateway_stub, clean_tables) -> Iterator[TestClient]:
    from api_app.main import create_app

    app = create_app(settings)
    with TestClient(app) as test_client:
        # Replace the real client after startup so no test can reach a gateway.
        app.state.gateway = gateway_stub
        yield test_client


@pytest.fixture
def registered_user(client) -> dict:
    response = client.post(
        "/v1/auth/register",
        json={
            "email": "owner@example.com",
            "password": "correct-horse-battery",
            "name": "Owner",
            "organization_name": "Acme Corp",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def conversation(client, registered_user) -> dict:
    response = client.post("/v1/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def read_sse():
    """Decode a completed SSE response body into ``(event, data)`` pairs."""

    def parse(response) -> list[tuple[str | None, str]]:
        events: list[tuple[str | None, str]] = []
        for block in response.text.split("\n\n"):
            name: str | None = None
            data: list[str] = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data.append(line[len("data:") :].strip())
            if data:
                events.append((name, "\n".join(data)))
        return events

    return parse
