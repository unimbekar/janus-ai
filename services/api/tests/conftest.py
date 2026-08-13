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

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set JANUS_TEST_DATABASE_URL (see make test-api)",
)

GATEWAY_TOKEN = "test-gateway-token"


def _app_database_url(owner_url: str) -> str:
    """Same database, but as the restricted role the service really uses."""
    without_scheme = owner_url.split("://", 1)[1]
    _, _, host_and_db = without_scheme.partition("@")
    return f"postgresql+asyncpg://janus_app:janus_app@{host_and_db}"


@pytest.fixture(scope="session")
def owner_url() -> str:
    assert TEST_DATABASE_URL
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def settings(owner_url):
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
    )


@pytest.fixture(scope="session", autouse=True)
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
async def db(settings) -> AsyncIterator[object]:
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


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(settings, migrated_database) -> AsyncIterator[None]:
    """Truncate between tests, as the owner so RLS does not hide leftovers."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.effective_migration_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE core.audit_events, core.api_keys, core.sessions, "
                "core.organization_members, core.team_members, core.teams, "
                "core.policies, core.organizations, core.users CASCADE"
            )
        )
    await engine.dispose()
    yield


@pytest.fixture
def gateway_stub():
    """A gateway that records calls instead of making them.

    The control plane must never reach a provider, so its tests must not reach a
    gateway either. What matters here is the context it forwards.
    """

    class GatewayStub:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.models_response: dict = {"object": "list", "data": []}
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

        async def chat_completion(self, payload, **kwargs) -> tuple[int, dict]:
            self.calls.append({"operation": "chat_completion", "payload": payload, **kwargs})
            return 200, self.completion_response

        async def stream_chat_completion(self, payload, **kwargs):
            self.calls.append({"operation": "stream", "payload": payload, **kwargs})

            async def generator():
                yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                yield b"data: [DONE]\n\n"

            return generator()

        async def health(self) -> bool:
            return True

        async def aclose(self) -> None:
            return None

    return GatewayStub()


@pytest.fixture
def client(settings, gateway_stub) -> Iterator[TestClient]:
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
