"""Deployment health tracking.

Health is the router's most important input after policy: a healthy-looking
catalog entry that cannot serve is worse than one that is honestly absent. Two
signals feed the state here — active probes and observed request outcomes — and
the observed signal wins, because a deployment that just failed a real request is
unhealthy regardless of what a probe said thirty seconds ago.

In-process and per-instance in Phase 1. Shared state in Redis, so every gateway
instance benefits from one instance's observations, lands in Phase 3 alongside
circuit breaking.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from janus_core.logging import get_logger
from janus_schemas.common import HealthState

from gateway_app.backends import BackendRegistry
from gateway_app.registry.records import Registry

logger = get_logger(__name__)


@dataclass(slots=True)
class DeploymentHealth:
    key: str
    state: HealthState
    consecutive_failures: int = 0
    last_latency_ms: int | None = None
    last_error: str | None = None
    last_probe_at: float | None = None
    last_success_at: float | None = None
    observations: int = 0

    @property
    def is_routable(self) -> bool:
        return self.state.is_routable


@dataclass(slots=True)
class HealthTracker:
    failure_threshold: int = 3
    _states: dict[str, DeploymentHealth] = field(default_factory=dict)

    def seed(self, registry: Registry) -> None:
        """Adopt the registry's declared starting state for new deployments."""
        for model in registry.models:
            for deployment in model.deployments:
                self._states.setdefault(
                    deployment.key,
                    DeploymentHealth(key=deployment.key, state=deployment.initial_health),
                )

    def state_for(self, key: str) -> HealthState:
        entry = self._states.get(key)
        return entry.state if entry else HealthState.READY

    def snapshot(self) -> dict[str, DeploymentHealth]:
        return dict(self._states)

    def availability_map(self) -> dict[str, HealthState]:
        return {key: entry.state for key, entry in self._states.items()}

    def _entry(self, key: str) -> DeploymentHealth:
        entry = self._states.get(key)
        if entry is None:
            entry = DeploymentHealth(key=key, state=HealthState.READY)
            self._states[key] = entry
        return entry

    def record_success(self, key: str, latency_ms: int | None = None) -> None:
        entry = self._entry(key)
        entry.consecutive_failures = 0
        entry.last_latency_ms = latency_ms
        entry.last_error = None
        entry.last_success_at = time.time()
        entry.observations += 1
        if entry.state in (HealthState.DEGRADED, HealthState.OFFLINE, HealthState.WARMING):
            logger.info("deployment_recovered", extra={"deployment": key, "from": entry.state})
            entry.state = HealthState.READY

    def record_failure(self, key: str, reason: str) -> None:
        entry = self._entry(key)
        entry.consecutive_failures += 1
        entry.last_error = reason
        entry.observations += 1

        if entry.consecutive_failures >= self.failure_threshold:
            new_state = HealthState.OFFLINE
        elif entry.state is HealthState.READY:
            new_state = HealthState.DEGRADED
        else:
            new_state = entry.state

        if new_state is not entry.state:
            logger.warning(
                "deployment_health_changed",
                extra={
                    "deployment": key,
                    "from": entry.state,
                    "to": new_state,
                    "consecutive_failures": entry.consecutive_failures,
                    "reason": reason,
                },
            )
            entry.state = new_state

    def record_probe(
        self, key: str, state: HealthState, latency_ms: int | None, detail: str | None
    ) -> None:
        entry = self._entry(key)
        entry.last_probe_at = time.time()
        entry.last_latency_ms = latency_ms

        # An active probe never overrules recent request failures: it takes
        # threshold-many real failures to go offline and a real success to return.
        if state is HealthState.READY and entry.consecutive_failures >= self.failure_threshold:
            return

        if state is not entry.state:
            logger.info(
                "deployment_probe_state_changed",
                extra={"deployment": key, "from": entry.state, "to": state, "detail": detail},
            )
        entry.state = state
        entry.last_error = detail if state is not HealthState.READY else None


async def probe_once(registry: Registry, backends: BackendRegistry, tracker: HealthTracker) -> None:
    """Probe every enabled deployment once."""
    for model in registry.models:
        for deployment in model.deployments:
            if not deployment.enabled:
                continue
            try:
                backend = backends.get(deployment.backend)
                report = await backend.health(deployment)
            except Exception as exc:
                tracker.record_probe(deployment.key, HealthState.OFFLINE, None, type(exc).__name__)
                continue
            tracker.record_probe(deployment.key, report.state, report.latency_ms, report.detail)


async def health_probe_loop(
    registry_provider: object,
    backends: BackendRegistry,
    tracker: HealthTracker,
    interval_seconds: float,
) -> None:
    """Background probe loop; cancelled on shutdown."""
    from gateway_app.registry.service import RegistryService

    assert isinstance(registry_provider, RegistryService)
    while True:
        try:
            await probe_once(registry_provider.current, backends, tracker)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("health_probe_cycle_failed")
        await asyncio.sleep(interval_seconds)
