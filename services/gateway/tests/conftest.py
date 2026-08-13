from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_TOKEN = "test-gateway-token"


@pytest.fixture(scope="session", autouse=True)
def _environment() -> Iterator[None]:
    """Tests run against the deterministic ``test`` environment only.

    No network, no GPU, no provider account — a test that needs one of those is
    a test that cannot run in CI.
    """
    previous = dict(os.environ)
    os.environ.update(
        {
            "JANUS_ENVIRONMENT": "test",
            "JANUS_REGISTRY_DIR": str(REPO_ROOT / "registry"),
            "JANUS_GATEWAY_SERVICE_TOKEN": SERVICE_TOKEN,
            "JANUS_HEALTH_PROBE_ENABLED": "false",
            "JANUS_LOG_LEVEL": "WARNING",
        }
    )
    yield
    os.environ.clear()
    os.environ.update(previous)


@pytest.fixture
def settings():
    from gateway_app.settings import GatewaySettings

    return GatewaySettings()


@pytest.fixture
def registry(settings):
    from gateway_app.registry.loader import load_registry

    return load_registry(settings.registry_dir, "test")


@pytest.fixture
def health():
    from gateway_app.health import HealthTracker

    return HealthTracker(failure_threshold=3)


@pytest.fixture
def resolver(health):
    from gateway_app.router.resolver import ModelResolver

    return ModelResolver(health)


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    from gateway_app.main import create_app

    with TestClient(create_app(settings)) as test_client:
        test_client.headers.update(
            {
                "Authorization": f"Bearer {SERVICE_TOKEN}",
                "X-Janus-Organization-Id": "org_test",
                "X-Janus-Service": "janus-api",
            }
        )
        yield test_client


@pytest.fixture
def anonymous_client(settings) -> Iterator[TestClient]:
    from gateway_app.main import create_app

    with TestClient(create_app(settings)) as test_client:
        yield test_client
