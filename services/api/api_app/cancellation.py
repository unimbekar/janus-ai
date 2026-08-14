"""Cooperative cancellation for in-flight generations.

A browser that closes the connection cancels a stream by itself — Starlette
cancels the response task, the adapter closes the upstream call, and the partial
turn is saved. This registry exists for the other case: cancelling from somewhere
that is not holding the stream open, such as a second tab or a phone.

**This is per-instance state.** A cancel request only reaches a generation running
in the same process, so with several API replicas behind a load balancer it can
miss. That is acceptable for Phase 2 — the common path is the client's own
disconnect, which always works — and it is the reason Phase 3 introduces Redis
pub/sub rather than the reason to add Redis now.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from janus_core.logging import get_logger

logger = get_logger(__name__)

#: How long a cancel request stays honored if the generation it names never
#: checks in. Without an expiry, a cancel for a stream that already ended would
#: sit in memory and could stop the next one.
CANCEL_TTL_SECONDS = 300.0


@dataclass
class CancellationRegistry:
    _active: dict[str, set[str]] = field(default_factory=dict)
    _cancelled: dict[str, float] = field(default_factory=dict)

    def begin(self, conversation_id: str, message_id: str) -> None:
        self._active.setdefault(conversation_id, set()).add(message_id)
        self._prune()

    def finish(self, conversation_id: str, message_id: str) -> None:
        running = self._active.get(conversation_id)
        if running is not None:
            running.discard(message_id)
            if not running:
                self._active.pop(conversation_id, None)
        self._cancelled.pop(message_id, None)

    def cancel(self, conversation_id: str) -> int:
        """Ask every generation in this conversation to stop. Returns how many."""
        running = self._active.get(conversation_id, set())
        now = time.monotonic()
        for message_id in running:
            self._cancelled[message_id] = now
        if running:
            logger.info(
                "generation_cancel_requested",
                extra={"conversation_id": conversation_id, "generations": len(running)},
            )
        return len(running)

    def is_cancelled(self, message_id: str) -> bool:
        return message_id in self._cancelled

    def _prune(self) -> None:
        cutoff = time.monotonic() - CANCEL_TTL_SECONDS
        for message_id, requested_at in list(self._cancelled.items()):
            if requested_at < cutoff:
                del self._cancelled[message_id]
