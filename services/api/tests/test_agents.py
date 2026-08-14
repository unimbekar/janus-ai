"""Agents, runs, checkpoints, and the responses surface."""

from __future__ import annotations


def test_create_and_list_agents(client, registered_user) -> None:
    created = client.post(
        "/v1/agents",
        json={
            "name": "Briefing",
            "slug": "briefing",
            "instructions": "Answer from retrieved context. Never invent citations.",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["slug"] == "briefing"
    assert body["status"] == "draft"
    assert body["current_version"] == 1

    listed = client.get("/v1/agents").json()
    assert listed["data"][0]["id"] == body["id"]


def test_invalid_slug_is_rejected(client, registered_user) -> None:
    response = client.post("/v1/agents", json={"name": "Bad", "slug": "Not Valid"})
    assert response.status_code == 400


def test_publish_then_run_completes_with_a_checkpoint(client, registered_user) -> None:
    agent = client.post(
        "/v1/agents", json={"name": "Clock", "slug": "clock-agent", "tools": ["clock"]}
    ).json()
    published = client.post(f"/v1/agents/{agent['id']}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    run = client.post(
        f"/v1/agents/{agent['id']}/runs",
        json={"input": "What time is it in UTC?"},
    )
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["status"] == "completed"
    assert body["output"] == "stub"
    assert body["step_count"] >= 2

    detail = client.get(f"/v1/agents/runs/{body['id']}").json()
    nodes = [step["node"] for step in detail["steps"]]
    assert "tool" in nodes
    assert "compose" in nodes
    assert any(step["tool"] == "clock" for step in detail["steps"])


def test_responses_without_an_agent_is_a_single_completion(client, registered_user) -> None:
    response = client.post("/v1/responses", json={"input": "Hello", "model": "auto"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["output"][0]["text"] == "stub"


def test_responses_with_an_agent_runs_it(client, registered_user) -> None:
    agent = client.post(
        "/v1/agents", json={"name": "Reply", "slug": "reply-agent", "tools": []}
    ).json()
    response = client.post(
        "/v1/responses", json={"input": "Summarize this.", "agent_id": agent["id"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["janus"]["step_count"] >= 1


def test_agent_run_uses_knowledge_when_attached(client, registered_user) -> None:
    base = client.post("/v1/knowledge-bases", json={"name": "Playbook"}).json()
    ingest = client.post(
        f"/v1/knowledge-bases/{base['id']}/documents",
        json={
            "title": "Hours",
            "content": "The office opens at 09:00 UTC on weekdays.",
        },
    )
    assert ingest.status_code == 201, ingest.text

    agent = client.post(
        "/v1/agents",
        json={
            "name": "Support",
            "slug": "support-agent",
            "knowledge_base_ids": [base["id"]],
            "tools": ["knowledge_search"],
        },
    ).json()
    run = client.post(
        f"/v1/agents/{agent['id']}/runs",
        json={"input": "When does the office open?"},
    ).json()
    assert run["status"] == "completed"
    assert run["citations"]
    assert run["citations"][0]["document_id"]
