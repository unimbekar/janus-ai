"""Public API keys, rate limits, and operator registry reload."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gateway_app.auth import ApiKeyIdentity

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_TOKEN = "test-gateway-token"


class _Auth:
    async def authenticate(self, key: str) -> ApiKeyIdentity:
        assert key.startswith("jsk_")
        return ApiKeyIdentity(
            id="key_test",
            organization_id="org_test",
            scopes=("inference",),
            mode_ceiling=None,
        )


@pytest.fixture
def public_client(monkeypatch) -> Iterator[TestClient]:
    from gateway_app.main import create_app
    from gateway_app.settings import GatewaySettings

    monkeypatch.setenv("JANUS_RATE_LIMIT_PER_MINUTE", "1")
    settings = GatewaySettings(
        registry_dir=REPO_ROOT / "registry",
        gateway_service_token=SERVICE_TOKEN,
        health_probe_enabled=False,
        rate_limit_per_minute=1,
        public_api_enabled=True,
    )
    with TestClient(create_app(settings)) as client:
        client.app.state.api_key_auth = _Auth()
        yield client


def test_public_api_keys_are_rate_limited(public_client) -> None:
    headers = {"Authorization": "Bearer jsk_live_example"}
    first = public_client.get("/v1/models", headers=headers)
    second = public_client.get("/v1/models", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limit_exceeded"


def test_registry_reload_swaps_the_snapshot(client) -> None:
    before = client.get("/v1/models").json()
    response = client.post("/internal/registry/reload")

    assert response.status_code == 200
    assert response.json()["environment"] == "test"
    assert response.json()["deployment_count"] >= 2
    after = client.get("/v1/models").json()
    assert {item["id"] for item in after["data"]} == {item["id"] for item in before["data"]}
