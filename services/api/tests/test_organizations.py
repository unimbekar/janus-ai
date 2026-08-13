"""Organizations, membership, and the inference passthrough."""

from __future__ import annotations


def test_current_organization(client, registered_user) -> None:
    body = client.get("/v1/organizations/current").json()

    assert body["name"] == "Acme Corp"
    assert body["role"] == "owner"


def test_owner_can_change_the_execution_mode(client, registered_user) -> None:
    body = client.patch("/v1/organizations/current", json={"default_mode": "private"}).json()

    assert body["default_mode"] == "private"


def test_members_list_includes_the_owner(client, registered_user) -> None:
    members = client.get("/v1/organizations/current/members").json()

    assert len(members) == 1
    assert members[0]["role"] == "owner"


def test_adding_a_member_requires_an_existing_account(client, registered_user) -> None:
    response = client.post(
        "/v1/organizations/current/members",
        json={"email": "nobody@example.com", "role": "member"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"
    # The error says what is missing and when invitations arrive, rather than
    # pretending the feature exists.
    assert response.json()["error"]["details"]["available_from_phase"] == 9


def test_adding_an_existing_user_as_a_member(client, registered_user) -> None:
    owner_cookies = dict(client.cookies)
    client.cookies.clear()
    client.post(
        "/v1/auth/register",
        json={"email": "analyst@example.com", "password": "another-long-password"},
    )
    client.cookies.clear()
    client.cookies.update(owner_cookies)

    member = client.post(
        "/v1/organizations/current/members",
        json={"email": "analyst@example.com", "role": "member"},
    ).json()

    assert member["role"] == "member"
    assert len(client.get("/v1/organizations/current/members").json()) == 2


def test_non_admin_cannot_change_organization_settings(client, registered_user) -> None:
    owner_cookies = dict(client.cookies)

    client.cookies.clear()
    client.post(
        "/v1/auth/register",
        json={"email": "viewer@example.com", "password": "yet-another-long-pass"},
    )
    client.cookies.clear()
    client.cookies.update(owner_cookies)
    client.post(
        "/v1/organizations/current/members",
        json={"email": "viewer@example.com", "role": "viewer"},
    )

    # Sign in as the viewer and switch into the shared organization.
    organization_id = client.get("/v1/organizations/current").json()["id"]
    client.cookies.clear()
    client.post(
        "/v1/auth/login",
        json={"email": "viewer@example.com", "password": "yet-another-long-pass"},
    )
    client.post("/v1/auth/switch-organization", json={"organization_id": organization_id})

    response = client.patch("/v1/organizations/current", json={"default_mode": "cloud"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"


def test_model_catalog_is_requested_with_the_organization_policy(
    client, registered_user, gateway_stub
) -> None:
    client.patch("/v1/organizations/current", json={"default_mode": "sovereign"})
    client.get("/v1/models")

    call = gateway_stub.calls[-1]
    assert call["operation"] == "list_models"
    assert call["mode"].value == "sovereign"
    assert call["organization_id"].startswith("org_")


def test_chat_passthrough_forwards_policy_context(client, registered_user, gateway_stub) -> None:
    response = client.post(
        "/v1/chat",
        json={"model": "auto", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    call = gateway_stub.calls[-1]
    assert call["operation"] == "chat_completion"
    assert call["classification"].value == "INTERNAL"
    assert call["request_id"].startswith("rq_")
    # The control plane forwards; it never selects a model itself.
    assert call["payload"]["model"] == "auto"


def test_api_key_mode_ceiling_narrows_but_never_widens(
    client, registered_user, gateway_stub
) -> None:
    client.patch("/v1/organizations/current", json={"default_mode": "cloud"})
    key = client.post(
        "/v1/organizations/current/api-keys",
        json={"name": "restricted", "mode_ceiling": "sovereign"},
    ).json()["key"]

    client.cookies.clear()
    client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})

    # The key's ceiling is more restrictive than the organization default, so it
    # wins. The reverse can never happen.
    assert gateway_stub.calls[-1]["mode"].value == "sovereign"


def test_chat_requires_authentication(client) -> None:
    client.cookies.clear()
    response = client.post(
        "/v1/chat", json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
    )

    assert response.status_code == 401


def test_readiness_reports_dependencies(client) -> None:
    response = client.get("/readyz")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["schema"] == "ok"


def test_readiness_fails_on_a_schema_the_service_does_not_know(client) -> None:
    """A deploy that outran its migration must leave the load balancer pool.

    Reporting ready because the connection succeeded would send traffic to a
    service whose every query fails on a missing or renamed table.
    """

    async def stale_version() -> str:
        return "0000"

    client.app.state.db.schema_version = stale_version

    response = client.get("/readyz")
    body = response.json()

    assert response.status_code == 503
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == "ok"
    assert "expected 0001" in body["checks"]["schema"]


def test_readiness_fails_when_the_schema_is_missing(client) -> None:
    async def no_version() -> None:
        return None

    client.app.state.db.schema_version = no_version

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["schema"] == "unavailable"


def test_liveness_needs_no_dependencies(client) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
