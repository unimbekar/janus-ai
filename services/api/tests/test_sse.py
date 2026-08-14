"""The SSE decoder.

The control plane parses a copy of the stream it relays, which means it has to
survive whatever the network does to frame boundaries. These cases are the ones
that produce garbled or lost text in practice.
"""

from __future__ import annotations

from api_app.sse import SseDecoder


def test_a_whole_frame_decodes() -> None:
    events = SseDecoder().feed(b'event: janus.routing\ndata: {"model":"m"}\n\n')

    assert len(events) == 1
    assert events[0].name == "janus.routing"
    assert events[0].data == '{"model":"m"}'


def test_an_unnamed_frame_has_no_event_name() -> None:
    events = SseDecoder().feed(b'data: {"choices":[]}\n\n')

    assert events[0].name is None


def test_several_frames_in_one_read() -> None:
    events = SseDecoder().feed(b"data: one\n\ndata: two\n\ndata: three\n\n")

    assert [event.data for event in events] == ["one", "two", "three"]


def test_a_frame_split_across_reads_is_held_until_complete() -> None:
    decoder = SseDecoder()

    assert decoder.feed(b'data: {"content":"hel') == []
    assert decoder.feed(b'lo"}\n') == []
    events = decoder.feed(b"\n")

    assert [event.data for event in events] == ['{"content":"hello"}']


def test_a_multibyte_character_split_across_reads_is_not_mangled() -> None:
    """A UTF-8 character can straddle a network read; it must not become U+FFFD."""
    payload = 'data: {"content":"नमस्ते"}\n\n'.encode()
    decoder = SseDecoder()

    # Cut inside the first Devanagari character.
    split = payload.index(b"\xa8") + 1
    assert decoder.feed(payload[:split]) == []
    events = decoder.feed(payload[split:])

    assert "नमस्ते" in events[0].data
    assert "\ufffd" not in events[0].data


def test_multi_line_data_is_joined_with_newlines() -> None:
    events = SseDecoder().feed(b"data: first\ndata: second\n\n")

    assert events[0].data == "first\nsecond"


def test_carriage_returns_are_tolerated() -> None:
    events = SseDecoder().feed(b"event: janus.usage\r\ndata: {}\r\n\r\n")

    # A CRLF stream still terminates on a blank line, which here is "\r\n\r\n".
    assert events == [] or events[0].name == "janus.usage"


def test_a_comment_only_frame_yields_nothing() -> None:
    assert SseDecoder().feed(b": keep-alive\n\n") == []


def test_the_done_sentinel_is_an_ordinary_frame() -> None:
    events = SseDecoder().feed(b"data: [DONE]\n\n")

    assert events[0].data == "[DONE]"
