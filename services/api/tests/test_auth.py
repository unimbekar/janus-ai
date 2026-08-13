"""Registration, sign-in, sessions, and API keys."""

from __future__ import annotations

CREDENTIALS = {"email": "owner@example.com", "password": "correct-horse-battery"}


def test_registration_creates_a_user_and_an_organization(client) -> None:
    body = client.post(
        "/v1/auth/register",
        json={**CREDENTIALS, "name": "Owner", "organization_name": "Acme Corp"},
    ).json()

    assert body["user"]["email"] == "owner@example.com"
    assert body["organization"]["name"] == "Acme Corp"
    assert body["organization"]["slug"] == "acme-corp"
    # Every user has a tenant from the moment they exist.
    assert body["organization"]["role"] == "owner"
    assert body["organization"]["default_mode"] == "auto"


def test_registration_sets_an_httponly_session_cookie(client) -> None:
    response = client.post("/v1/auth/register", json=CREDENTIALS)

    cookie = response.headers["set-cookie"]
    assert "janus_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie.replace("Lax", "lax")


def test_password_is_never_returned_or_echoed(client) -> None:
    response = client.post("/v1/auth/register", json=CREDENTIALS)

    assert "correct-horse-battery" not in response.text
    assert "password" not in response.json()["user"]


def test_short_passwords_are_rejected(client) -> None:
    response = client.post("/v1/auth/register", json={"email": "a@b.com", "password": "short"})

    assert response.status_code == 400


def test_duplicate_email_is_a_conflict(client, registered_user) -> None:
    response = client.post("/v1/auth/register", json=CREDENTIALS)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


def test_login_succeeds_with_correct_credentials(client, registered_user) -> None:
    client.cookies.clear()
    response = client.post("/v1/auth/login", json=CREDENTIALS)

    assert response.status_code == 200
    assert response.json()["organization"]["slug"] == "acme-corp"


def test_login_failures_are_indistinguishable(client, registered_user) -> None:
    """Wrong password and unknown account must look the same to an attacker."""
    client.cookies.clear()
    wrong_password = client.post(
        "/v1/auth/login", json={**CREDENTIALS, "password": "wrong-but-long-enough"}
    )
    unknown_user = client.post(
        "/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-but-long-enough"}
    )

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown_user.json()["error"]["message"]
    assert wrong_password.json()["error"]["code"] == unknown_user.json()["error"]["code"]


def test_session_endpoint_returns_the_current_context(client, registered_user) -> None:
    body = client.get("/v1/auth/session").json()

    assert body["user"]["email"] == "owner@example.com"
    assert body["organization"]["role"] == "owner"
    assert len(body["organizations"]) == 1


def test_unauthenticated_requests_are_rejected(client) -> None:
    client.cookies.clear()
    response = client.get("/v1/auth/session")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_credentials"


def test_logout_revokes_the_session(client, registered_user) -> None:
    assert client.post("/v1/auth/logout").status_code == 204
    # The cookie is cleared, but the important part is that replaying the token
    # fails even if a copy was kept.
    assert client.get("/v1/auth/session").status_code == 401


def test_revoked_session_token_cannot_be_replayed(client, registered_user) -> None:
    token = client.cookies.get("janus_session")
    client.post("/v1/auth/logout")

    client.cookies.set("janus_session", token)
    assert client.get("/v1/auth/session").status_code == 401


def test_switching_organizations_requires_membership(client, registered_user) -> None:
    response = client.post(
        "/v1/auth/switch-organization", json={"organization_id": "org_someone_else"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "organization_not_found"


def test_switching_to_a_second_organization(client, registered_user) -> None:
    created = client.post("/v1/organizations", json={"name": "Second Workspace"}).json()

    body = client.post(
        "/v1/auth/switch-organization", json={"organization_id": created["id"]}
    ).json()

    assert body["organization"]["id"] == created["id"]
    assert len(body["organizations"]) == 2


def test_api_key_is_shown_once_and_then_only_as_a_prefix(client, registered_user) -> None:
    created = client.post("/v1/organizations/current/api-keys", json={"name": "CI key"}).json()

    assert created["key"].startswith("jsk_live_")
    assert created["prefix"] == created["key"][: len(created["prefix"])]

    listed = client.get("/v1/organizations/current/api-keys").json()
    assert "key" not in listed[0]
    assert listed[0]["prefix"] == created["prefix"]


def test_api_key_authenticates_requests(client, registered_user) -> None:
    key = client.post("/v1/organizations/current/api-keys", json={"name": "CI key"}).json()["key"]

    client.cookies.clear()
    response = client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})

    assert response.status_code == 200


def test_invalid_api_key_is_rejected(client, registered_user) -> None:
    client.cookies.clear()
    response = client.get("/v1/models", headers={"Authorization": "Bearer jsk_live_nonsense"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_revoked_api_key_is_rejected(client, registered_user) -> None:
    created = client.post("/v1/organizations/current/api-keys", json={"name": "CI key"}).json()
    assert client.delete(f"/v1/organizations/current/api-keys/{created['id']}").status_code == 204

    cookies = dict(client.cookies)
    client.cookies.clear()
    response = client.get("/v1/models", headers={"Authorization": f"Bearer {created['key']}"})
    client.cookies.update(cookies)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "api_key_revoked"


def test_api_key_cannot_perform_administration(client, registered_user) -> None:
    """Keys carry scopes for inference, not roles for administration."""
    key = client.post("/v1/organizations/current/api-keys", json={"name": "CI key"}).json()["key"]

    client.cookies.clear()
    response = client.get(
        "/v1/organizations/current/api-keys", headers={"Authorization": f"Bearer {key}"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "session_required"
