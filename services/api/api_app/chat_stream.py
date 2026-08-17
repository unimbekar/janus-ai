"""Running one turn: prompt in, stream out, transcript written.

This is where Phase 2 differs from the Phase 1 passthrough. The bytes the browser
receives are still the gateway's own stream, relayed unchanged — the streaming
contract lives in exactly one place — but a copy is parsed on the way through so
the answer, its attribution, and its usage land in the database.

Two properties are deliberate and worth preserving:

**A turn is always finalized.** Normal completion, a model error, an explicit
cancel, and the user closing the tab all end at the same place: the assistant row
stops being ``streaming`` and keeps whatever text had arrived. A row stuck in
``streaming`` would be a permanent lie in someone's history.

**Failures arrive as events, not status codes.** The response has already begun by
the time a model can fail, so this endpoint reports problems as a terminal
``janus.error`` frame. Programmatic callers that want HTTP status codes use the
stateless ``/v1/chat`` endpoint instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from janus_core.errors import JanusError
from janus_core.logging import get_logger
from janus_schemas.common import Classification, ExecutionMode

from api_app.cancellation import CancellationRegistry
from api_app.conversations import Attribution, ConversationService
from api_app.db import Database
from api_app.gateway_client import GatewayClient
from api_app.sse import SseDecoder

logger = get_logger(__name__)

SSE_DONE = b"data: [DONE]\n\n"

#: Finalization tasks that outlived their request. Held so the event loop cannot
#: garbage-collect a write in progress when a client disconnects mid-stream.
_PENDING_WRITES: set[asyncio.Task[None]] = set()


@dataclass(frozen=True, slots=True)
class Turn:
    """Everything the runner needs, as values rather than ORM objects.

    The rows were written and committed in an earlier transaction; passing plain
    identifiers means nothing here can try to lazy-load against a closed session.
    """

    conversation_id: str
    organization_id: str
    user_id: str
    actor_id: str
    assistant_message_id: str
    sequence: int
    user_message_id: str | None = None


def sse_frame(event: str | None, payload: dict[str, Any]) -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


class ChatRunner:
    def __init__(
        self,
        db: Database,
        conversations: ConversationService,
        gateway: GatewayClient,
        cancellations: CancellationRegistry,
    ) -> None:
        self._db = db
        self._conversations = conversations
        self._gateway = gateway
        self._cancellations = cancellations

    async def run(
        self,
        turn: Turn,
        *,
        prompt: list[dict[str, str]],
        model: str,
        mode: ExecutionMode,
        classification: Classification,
        request_id: str,
        citations: list[dict] | None = None,
    ) -> AsyncIterator[bytes]:
        payload = {
            "model": model,
            "messages": prompt,
            "stream": True,
            "janus": {
                "conversation_id": turn.conversation_id,
                "routing": {"explain": True},
            },
        }

        collector = _TurnCollector()
        self._cancellations.begin(turn.conversation_id, turn.assistant_message_id)

        # The identifiers come first so the client can render the pending turn and
        # cancel it before a single token exists.
        yield sse_frame(
            "janus.message",
            {
                "conversation_id": turn.conversation_id,
                "user_message_id": turn.user_message_id,
                "assistant_message_id": turn.assistant_message_id,
                "sequence": turn.sequence,
            },
        )
        if citations:
            yield sse_frame("janus.citations", {"data": citations})

        try:
            async for chunk in self._gateway.stream_chat_completion(
                payload,
                organization_id=turn.organization_id,
                request_id=request_id,
                mode=mode,
                classification=classification,
                actor_id=turn.actor_id,
            ):
                collector.observe(chunk)
                yield chunk

                if self._cancellations.is_cancelled(turn.assistant_message_id):
                    collector.status = "cancelled"
                    yield sse_frame(
                        "janus.cancelled",
                        {
                            "assistant_message_id": turn.assistant_message_id,
                            "reason": "requested",
                        },
                    )
                    yield SSE_DONE
                    break
        except JanusError as error:
            collector.status = "error"
            collector.error = error.to_payload(request_id)["error"]
            yield sse_frame("janus.error", error.to_payload(request_id))
            yield SSE_DONE
        except (asyncio.CancelledError, GeneratorExit):
            # The client went away — cancelled task if the connection dropped,
            # ``aclose()`` if the response was torn down. The partial answer is
            # still worth keeping, so finalization happens in the finally block and
            # this propagates.
            collector.status = "cancelled"
            raise
        finally:
            await self._finalize(turn, collector)
            self._cancellations.finish(turn.conversation_id, turn.assistant_message_id)

    async def _finalize(self, turn: Turn, collector: _TurnCollector) -> None:
        """Write the turn, even if this request is being cancelled.

        Shielding matters: on a client disconnect the surrounding task is already
        cancelled, so an unshielded ``await`` here would abandon the write and
        leave the row ``streaming`` forever. The task is kept referenced and runs
        to completion detached.
        """
        task = asyncio.create_task(self._write(turn, collector))
        _PENDING_WRITES.add(task)
        task.add_done_callback(_PENDING_WRITES.discard)
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)

    async def _write(self, turn: Turn, collector: _TurnCollector) -> None:
        try:
            async with self._db.session(
                organization_id=turn.organization_id, user_id=turn.user_id
            ) as session:
                await self._conversations.finalize_assistant_message(
                    session,
                    turn.assistant_message_id,
                    text=collector.text,
                    status=collector.status,
                    attribution=collector.attribution,
                    input_tokens=collector.input_tokens,
                    output_tokens=collector.output_tokens,
                    finish_reason=collector.finish_reason,
                    error=collector.error,
                )
        except Exception:
            # A failed write must not surface as a broken stream; the answer was
            # already delivered. It does need to be loud in the logs.
            logger.error(
                "assistant_message_not_persisted",
                extra={
                    "conversation_id": turn.conversation_id,
                    "message_id": turn.assistant_message_id,
                    "status": collector.status,
                },
                exc_info=True,
            )


class _TurnCollector:
    """Reads the relayed stream to reconstruct what was said."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self.status = "complete"
        self.attribution = Attribution()
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.finish_reason: str | None = None
        self.error: dict[str, Any] | None = None
        self._decoder = SseDecoder()

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def observe(self, chunk: bytes) -> None:
        for event in self._decoder.feed(chunk):
            if event.data == "[DONE]":
                continue
            try:
                data = json.loads(event.data)
            except json.JSONDecodeError:
                continue
            self._apply(event.name, data)

    def _apply(self, name: str | None, data: dict[str, Any]) -> None:
        if name == "janus.routing":
            self.attribution = Attribution(
                model_slug=data.get("model"),
                deployment_key=data.get("deployment"),
                provider=data.get("provider"),
                privacy=data.get("privacy"),
                fallback_used=bool(data.get("fallback_used")),
                routing_explanation=data.get("routing_explanation"),
                request_id=data.get("request_id"),
            )
            return

        if name == "janus.usage":
            usage = data.get("usage") or {}
            self.input_tokens = usage.get("prompt_tokens")
            self.output_tokens = usage.get("completion_tokens")
            return

        if name == "janus.error":
            self.status = "error"
            self.error = data.get("error")
            return

        if name is None:
            for choice in data.get("choices") or []:
                delta = (choice.get("delta") or {}).get("content")
                if isinstance(delta, str):
                    self._parts.append(delta)
                if choice.get("finish_reason"):
                    self.finish_reason = choice["finish_reason"]
