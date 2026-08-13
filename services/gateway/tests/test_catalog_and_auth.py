"""Catalog endpoints and caller authentication."""

from __future__ import annotations


def test_liveness_needs_no_credentials(anonymous_client) -> None:
    response = anonymous_client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_reports_the_catalog(client) -> None:
    response = client.get("/readyz")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "ready"
    assert body["environment"] == "test"
    assert body["routable_deployments"] >= 1


def test_readiness_fails_with_no_routable_deployment(client) -> None:
    """An instance that can reach no model should leave the load balancer pool."""
    from janus_schemas.common import HealthState

    health = client.app.state.health
    for model in client.app.state.registry_service.current.models:
        for deployment in model.deployments:
            health.record_probe(deployment.key, HealthState.OFFLINE, None, "test")

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_missing_credentials_are_rejected(anonymous_client) -> None:
    response = anonymous_client.get("/v1/models", headers={"X-Janus-Organization-Id": "org_test"})

    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication"


def test_wrong_credentials_are_rejected(anonymous_client) -> None:
    response = anonymous_client.get(
        "/v1/models",
        headers={"Authorization": "Bearer wrong", "X-Janus-Organization-Id": "org_test"},
    )

    assert response.status_code == 401


def test_organization_context_is_required(anonymous_client) -> None:
    response = anonymous_client.get(
        "/v1/models", headers={"Authorization": "Bearer test-gateway-token"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_organization_context"


def test_model_list_shape(client) -> None:
    body = client.get("/v1/models").json()

    assert body["object"] == "list"
    entry = next(item for item in body["data"] if item["id"] == "janus/mock-reasoning")
    assert entry["object"] == "model"
    assert entry["owned_by"] == "janus"
    assert entry["janus"]["context_window"] == 131072
    assert "long_context" in entry["janus"]["capabilities"]
    assert entry["janus"]["deployments"][0]["key"] == "mock-reasoning-private"


def test_model_list_never_exposes_endpoints(client) -> None:
    body = client.get("/v1/models").text

    assert "endpoint" not in body
    assert "credentials_ref" not in body


def test_model_list_is_filtered_by_caller_policy(client) -> None:
    everything = client.get("/v1/models").json()
    assert {item["id"] for item in everything["data"]} == {
        "janus/mock-small",
        "janus/mock-reasoning",
    }

    sovereign = client.get("/v1/models", headers={"X-Janus-Mode": "sovereign"}).json()
    # A caller who cannot use the local mock is not shown it.
    assert {item["id"] for item in sovereign["data"]} == {"janus/mock-reasoning"}


def test_unverified_metadata_is_marked(client) -> None:
    entry = next(
        item for item in client.get("/v1/models").json()["data"] if item["id"] == "janus/mock-small"
    )

    assert entry["janus"]["metadata_verified"] is True
    assert entry["janus"]["notes"]


def test_model_detail(client) -> None:
    body = client.get("/v1/models/janus/mock-small").json()

    assert body["id"] == "janus/mock-small"
    assert body["janus"]["display_name"] == "Janus Mock Small"


def test_forbidden_model_looks_absent(client) -> None:
    """Policy is not disclosed through the catalog."""
    response = client.get("/v1/models/janus/mock-small", headers={"X-Janus-Mode": "sovereign"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


def test_provider_list(client) -> None:
    body = client.get("/v1/providers").json()

    janus = next(item for item in body["data"] if item["id"] == "janus")
    assert janus["model_count"] == 2


def test_deployment_health_view_hides_infrastructure(client) -> None:
    body = client.get("/internal/deployments").json()

    assert body["environment"] == "test"
    keys = {item["key"] for item in body["deployments"]}
    assert keys == {"mock-small-local", "mock-reasoning-private"}
    assert "endpoint" not in client.get("/internal/deployments").text
