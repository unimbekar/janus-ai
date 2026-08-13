"""The chat completions endpoint, end to end through the mock backend."""

from __future__ import annotations

import json

BASE = {"messages": [{"role": "user", "content": "Hello Janus"}]}


def _body(**overrides) -> dict:
    return {**BASE, **overrides}


def parse_sse(text: str) -> list[tuple[str | None, str]]:
    """Parse an SSE body into ``(event, data)`` pairs."""
    events: list[tuple[str | None, str]] = []
    for block in text.strip().split("\n\n"):
        event: str | None = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        if data_lines:
            events.append((event, "\n".join(data_lines)))
    return events


def test_non_streaming_completion(client) -> None:
    response = client.post("/v1/chat/completions", json=_body(model="auto"))

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] > 0


def test_response_reports_what_actually_served_the_request(client) -> None:
    response = client.post("/v1/chat/completions", json=_body(model="auto"))
    janus = response.json()["janus"]

    # A caller that sent "auto" must learn what served it.
    assert janus["model"] == "janus/mock-small"
    assert janus["deployment"] == "mock-small-local"
    assert janus["provider"] == "janus"
    assert janus["fallback_used"] is False
    assert janus["request_id"].startswith("rq_")


def test_internal_details_are_never_returned(client) -> None:
    response = client.post("/v1/chat/completions", json=_body(model="auto"))
    body = response.text

    assert "endpoint" not in body
    assert "credentials" not in body
    assert "localhost" not in body


def test_explanation_is_opt_in(client) -> None:
    without = client.post("/v1/chat/completions", json=_body(model="auto"))
    assert without.json()["janus"]["routing_explanation"] is None

    with_explanation = client.post(
        "/v1/chat/completions",
        json=_body(model="auto", janus={"routing": {"explain": True}}),
    )
    assert with_explanation.json()["janus"]["routing_explanation"]


def test_streaming_event_order(client) -> None:
    with client.stream(
        "POST", "/v1/chat/completions", json=_body(model="auto", stream=True)
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = parse_sse("".join(response.iter_text()))

    names = [event for event, _ in events]
    assert names[0] == "janus.routing"
    assert names[-2] == "janus.usage"
    assert events[-1] == (None, "[DONE]")

    # Routing metadata arrives before any content, so the UI can attribute the
    # answer while it is still streaming.
    routing = json.loads(events[0][1])
    assert routing["model"] == "janus/mock-small"

    contents = [
        json.loads(data)["choices"][0]["delta"].get("content", "")
        for event, data in events
        if event is None and data != "[DONE]"
    ]
    assert "".join(contents)

    usage = json.loads(events[-2][1])
    assert usage["usage"]["completion_tokens"] > 0
    assert usage["ttft_ms"] is not None


def test_streaming_and_non_streaming_agree_on_content(client) -> None:
    body = _body(model="janus/mock-small")
    plain = client.post("/v1/chat/completions", json=body).json()

    with client.stream("POST", "/v1/chat/completions", json={**body, "stream": True}) as response:
        events = parse_sse("".join(response.iter_text()))

    streamed = "".join(
        json.loads(data)["choices"][0]["delta"].get("content", "") or ""
        for event, data in events
        if event is None and data != "[DONE]"
    )
    assert streamed == plain["choices"][0]["message"]["content"]


def test_fallback_to_the_next_candidate(client) -> None:
    """A failure before the first token moves to the next eligible candidate."""
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "__fail_on__:mock-small-local hello"}],
        },
    )

    assert response.status_code == 200
    janus = response.json()["janus"]
    assert janus["deployment"] == "mock-reasoning-private"
    assert janus["fallback_used"] is True


def test_all_candidates_failing_is_a_503(client) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "__fail__"}]},
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "all_candidates_failed"
    assert error["retryable"] is True


def test_non_retryable_provider_error_is_not_retried(client) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "__auth_fail__"}]},
    )

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "provider_auth_failed"
    # The credential itself is never echoed.
    assert "token" not in error["message"].lower()


def test_pinned_deployment_is_never_substituted(client) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "janus/mock-small@mock-small-local",
            "messages": [{"role": "user", "content": "__fail_on__:mock-small-local hi"}],
        },
    )

    assert response.status_code == 503


def test_request_mode_can_tighten_policy(client) -> None:
    response = client.post(
        "/v1/chat/completions",
        json=_body(model="auto", janus={"mode": "sovereign"}),
    )

    assert response.json()["janus"]["deployment"] == "mock-reasoning-private"


def test_request_cannot_loosen_caller_policy(client) -> None:
    """Asking for `cloud` under a `sovereign` caller policy stays sovereign."""
    response = client.post(
        "/v1/chat/completions",
        json=_body(model="auto", janus={"mode": "cloud"}),
        headers={"X-Janus-Mode": "sovereign"},
    )

    assert response.status_code == 200
    assert response.json()["janus"]["mode"] == "sovereign"
    assert response.json()["janus"]["deployment"] == "mock-reasoning-private"


def test_confidential_data_cannot_be_forced_to_a_provider(client) -> None:
    response = client.post(
        "/v1/chat/completions",
        json=_body(model="auto", janus={"mode": "cloud", "classification": "RESTRICTED"}),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "no_eligible_model"


def test_unsatisfiable_requirements_return_a_typed_error(client) -> None:
    response = client.post(
        "/v1/chat/completions",
        json=_body(model="auto", janus={"requirements": {"capabilities": ["vision"]}}),
    )

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["type"] == "policy_violation"
    assert "hint" in error["details"]


def test_unknown_model_is_a_404(client) -> None:
    response = client.post("/v1/chat/completions", json=_body(model="gpt-imaginary"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


def test_multilingual_round_trip(client) -> None:
    """Non-Latin input must survive the pipeline byte-for-byte."""
    text = "इस अनुबंध का सारांश दें। 日本語もテスト。"
    response = client.post(
        "/v1/chat/completions",
        json={"model": "janus/mock-reasoning", "messages": [{"role": "user", "content": text}]},
    )

    assert response.status_code == 200
    assert text.splitlines()[-1][:40] in response.json()["choices"][0]["message"]["content"]


def test_validation_errors_use_the_janus_envelope(client) -> None:
    response = client.post("/v1/chat/completions", json={"model": "auto", "messages": []})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request"
    assert error["request_id"].startswith("rq_")


def test_system_only_conversation_is_rejected(client) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "system", "content": "be nice"}]},
    )

    assert response.status_code == 400


def test_unknown_janus_option_is_rejected(client) -> None:
    """The extension is strict, so a typo is an error rather than silence."""
    response = client.post(
        "/v1/chat/completions", json=_body(model="auto", janus={"nmode": "private"})
    )

    assert response.status_code == 400


def test_request_id_is_echoed_and_reused(client) -> None:
    response = client.post(
        "/v1/chat/completions",
        json=_body(model="auto"),
        headers={"X-Janus-Request-Id": "rq_caller_supplied"},
    )

    assert response.headers["X-Janus-Request-Id"] == "rq_caller_supplied"
    assert response.json()["janus"]["request_id"] == "rq_caller_supplied"
