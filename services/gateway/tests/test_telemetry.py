"""Durable routing decisions are recorded, never just logged."""

from __future__ import annotations

from gateway_app.telemetry.writer import UsageWrite
from janus_schemas.chat import Usage


class RecordingTelemetry:
    def __init__(self) -> None:
        self.decisions: list[dict] = []
        self.usage: list[UsageWrite] = []

    async def record_routing_decision(self, **kwargs) -> None:
        self.decisions.append(kwargs)

    async def record_usage(self, payload: UsageWrite) -> None:
        self.usage.append(payload)


def test_a_successful_completion_writes_a_decision_and_usage(client) -> None:
    telemetry = RecordingTelemetry()
    client.app.state.executor._telemetry = telemetry

    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 200
    assert telemetry.decisions[0]["requested_model"] == "auto"
    assert telemetry.decisions[0]["selected"].model.slug in {
        "janus/mock-small",
        "janus/mock-reasoning",
    }
    assert telemetry.decisions[0]["resolution"].routing_reason == "auto"
    assert isinstance(telemetry.usage[0].usage, Usage)
