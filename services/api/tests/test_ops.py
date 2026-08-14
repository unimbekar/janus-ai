"""Usage, policies, audit, and deployment health."""

from __future__ import annotations


def test_usage_starts_at_zero(client, registered_user) -> None:
    body = client.get("/v1/usage").json()
    assert body["requests"] == 0
    assert body["input_tokens"] == 0
    assert body["output_tokens"] == 0


def test_owner_can_create_and_list_a_policy(client, registered_user) -> None:
    created = client.post(
        "/v1/policies",
        json={"mode": "private", "max_cost_usd_per_day": 25.0},
    )
    assert created.status_code == 200, created.text
    assert created.json()["mode"] == "private"

    listed = client.get("/v1/policies").json()
    assert listed["data"]
    assert listed["data"][0]["limits"]["max_cost_usd_per_day"] == 25.0


def test_audit_events_are_visible_to_an_admin(client, registered_user) -> None:
    body = client.get("/v1/audit-events").json()
    assert "data" in body
    assert any(event["action"] for event in body["data"]) or body["data"] == []


def test_deployments_hide_infrastructure(client, registered_user, gateway_stub) -> None:
    gateway_stub.models_response = {
        "object": "list",
        "data": [
            {
                "id": "janus/mock-small",
                "janus": {
                    "deployments": [
                        {
                            "key": "mock-small-local",
                            "privacy": "local",
                            "availability": "ready",
                            "accelerator": "cpu",
                            "region": None,
                            "endpoint": "http://secret.internal:8000",
                        }
                    ]
                },
            }
        ],
    }
    body = client.get("/v1/deployments").json()
    assert body["data"][0]["key"] == "mock-small-local"
    assert body["data"][0]["accelerator"] == "cpu"
    assert "endpoint" not in body["data"][0]
    assert "secret.internal" not in client.get("/v1/deployments").text
