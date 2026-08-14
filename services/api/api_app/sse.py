"""Incremental Server-Sent Events decoding.

The control plane relays the gateway's stream byte for byte — the streaming
contract is defined in exactly one place — but it also has to *understand* the
stream to persist what the model said. Both at once means parsing a copy of the
bytes as they pass through, which requires a decoder that tolerates frames split
across network reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SseEvent:
    """One decoded frame. ``name`` is ``None`` for an unnamed data-only event."""

    name: str | None
    data: str


@dataclass
class SseDecoder:
    """Accumulates bytes and yields whole frames.

    A frame ends at a blank line, and nothing is emitted until that terminator
    arrives — so a chunk boundary in the middle of a JSON payload cannot produce a
    half-parsed event.
    """

    _buffer: str = ""
    _pending: bytes = field(default=b"")

    def feed(self, chunk: bytes) -> list[SseEvent]:
        # Decode conservatively: a multi-byte character can straddle two reads, so
        # an incomplete tail is held back rather than replaced with U+FFFD.
        raw = self._pending + chunk
        try:
            text = raw.decode("utf-8")
            self._pending = b""
        except UnicodeDecodeError as exc:
            text = raw[: exc.start].decode("utf-8")
            self._pending = raw[exc.start :]

        self._buffer += text
        blocks = self._buffer.split("\n\n")
        self._buffer = blocks.pop()

        events: list[SseEvent] = []
        for block in blocks:
            event = _parse_block(block)
            if event is not None:
                events.append(event)
        return events


def _parse_block(block: str) -> SseEvent | None:
    name: str | None = None
    data_lines: list[str] = []

    for line in block.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("event:"):
            name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())

    if not data_lines:
        return None
    return SseEvent(name=name, data="\n".join(data_lines))
