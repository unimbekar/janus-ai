"""Conversations: persistence, streaming, cancellation, regeneration.

These tests are about the guarantees a chat product lives or dies by. History
survives a failed model call. Two tabs sending at once do not interleave into
nonsense. A cancelled answer keeps the words that arrived. A regenerated answer
does not erase the one it replaces.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from api_app.cancellation import CancellationRegistry
from api_app.chat_stream import ChatRunner, Turn
from api_app.conversations import ConversationService, content_text
from api_app.models import Conversation, Message
from janus_core.errors import UnavailableError
from janus_schemas.common import Classification, ExecutionMode
from sqlalchemy import select


def send(client, conversation_id: str, content: str, **body):
    return client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": content, **body},
    )


# ---------------------------------------------------------------- lifecycle


def test_a_new_conversation_starts_empty(client, conversation) -> None:
    detail = client.get(f"/v1/conversations/{conversation['id']}").json()

    assert detail["messages"] == []
    assert detail["message_count"] == 0
    assert detail["title"] is None


def test_the_first_message_titles_the_conversation(client, conversation) -> None:
    send(client, conversation["id"], "Explain our refund policy in plain English")

    detail = client.get(f"/v1/conversations/{conversation['id']}").json()
    assert detail["title"] == "Explain our refund policy in plain English"


def test_conversations_are_listed_most_recently_active_first(client, registered_user) -> None:
    first = client.post("/v1/conversations", json={"title": "older"}).json()
    second = client.post("/v1/conversations", json={"title": "newer"}).json()
    send(client, first["id"], "this makes the older one the most recent")

    listing = client.get("/v1/conversations").json()
    assert [item["id"] for item in listing["data"]] == [first["id"], second["id"]]


def test_listing_pages_with_an_opaque_cursor(client, registered_user) -> None:
    created = [client.post("/v1/conversations", json={}).json()["id"] for _ in range(5)]

    first_page = client.get("/v1/conversations", params={"limit": 2}).json()
    assert len(first_page["data"]) == 2
    assert first_page["next_cursor"]

    second_page = client.get(
        "/v1/conversations", params={"limit": 2, "cursor": first_page["next_cursor"]}
    ).json()
    seen = [item["id"] for item in first_page["data"] + second_page["data"]]

    assert len(set(seen)) == 4, "a page boundary must not repeat a conversation"
    assert set(seen) <= set(created)


def test_a_malformed_cursor_is_rejected(client, registered_user) -> None:
    response = client.get("/v1/conversations", params={"cursor": "not-a-cursor"})

    assert response.status_code == 400
    assert response.json()["error"]["param"] == "cursor"


def test_deleting_a_conversation_hides_it_from_history(client, conversation) -> None:
    assert client.delete(f"/v1/conversations/{conversation['id']}").status_code == 204

    assert client.get("/v1/conversations").json()["data"] == []
    assert client.get(f"/v1/conversations/{conversation['id']}").status_code == 404


def test_a_pinned_model_is_used_for_the_next_turn(client, conversation, gateway_stub) -> None:
    client.patch(
        f"/v1/conversations/{conversation['id']}",
        json={"pinned_model": "janus/mock-reasoning"},
    )
    send(client, conversation["id"], "hello")

    assert gateway_stub.calls[-1]["payload"]["model"] == "janus/mock-reasoning"


def test_a_per_message_model_overrides_the_pin(client, conversation, gateway_stub) -> None:
    client.patch(
        f"/v1/conversations/{conversation['id']}", json={"pinned_model": "janus/mock-reasoning"}
    )
    send(client, conversation["id"], "hello", model="janus/mock-small")

    assert gateway_stub.calls[-1]["payload"]["model"] == "janus/mock-small"


def test_clearing_the_pin_returns_to_automatic_routing(client, conversation, gateway_stub) -> None:
    client.patch(f"/v1/conversations/{conversation['id']}", json={"pinned_model": "janus/x"})
    client.patch(f"/v1/conversations/{conversation['id']}", json={"clear_pinned_model": True})
    send(client, conversation["id"], "hello")

    assert gateway_stub.calls[-1]["payload"]["model"] == "auto"


# ----------------------------------------------------------------- streaming


def test_sending_a_message_streams_ids_then_routing_then_content(
    client, conversation, read_sse
) -> None:
    response = send(client, conversation["id"], "hello")
    assert response.status_code == 200
    events = read_sse(response)

    names = [name for name, _ in events]
    assert names[0] == "janus.message", "the client needs the ids before anything else"
    assert names[1] == "janus.routing", "attribution arrives before the first token"
    assert names[-1] is None and events[-1][1] == "[DONE]"

    ids = json.loads(events[0][1])
    assert ids["conversation_id"] == conversation["id"]
    assert ids["user_message_id"].startswith("msg_")
    assert ids["assistant_message_id"].startswith("msg_")


def test_the_answer_is_persisted_with_its_attribution(client, conversation) -> None:
    send(client, conversation["id"], "hello")

    messages = client.get(f"/v1/conversations/{conversation['id']}").json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert [message["sequence"] for message in messages] == [1, 2]

    answer = messages[1]
    assert answer["content"] == "Hello there"
    assert answer["status"] == "complete"
    assert answer["model"] == "janus/mock-small"
    assert answer["deployment"] == "mock-small-local"
    assert answer["privacy"] == "local"
    assert answer["input_tokens"] == 7
    assert answer["output_tokens"] == 3
    assert answer["finish_reason"] == "stop"
    assert answer["routing_explanation"] == "Chosen for this test."


def test_history_is_replayed_to_the_gateway(client, conversation, gateway_stub) -> None:
    send(client, conversation["id"], "first question")
    send(client, conversation["id"], "second question")

    prompt = gateway_stub.calls[-1]["payload"]["messages"]
    assert [message["role"] for message in prompt] == ["user", "assistant", "user"]
    assert prompt[0]["content"] == "first question"
    assert prompt[-1]["content"] == "second question"


def test_the_conversation_id_travels_with_the_request(client, conversation, gateway_stub) -> None:
    send(client, conversation["id"], "hello")

    assert gateway_stub.calls[-1]["payload"]["janus"]["conversation_id"] == conversation["id"]


def test_non_latin_script_survives_the_round_trip(client, conversation) -> None:
    text = "नमस्ते — 日本語 — Ωμέγα — emoji 🎯"
    send(client, conversation["id"], text)

    messages = client.get(f"/v1/conversations/{conversation['id']}").json()["messages"]
    assert messages[0]["content"] == text


def test_a_model_failure_keeps_the_question_and_records_the_error(
    client, conversation, gateway_stub, read_sse
) -> None:
    gateway_stub.stream_error = UnavailableError("Every eligible model failed for this request.")

    events = read_sse(send(client, conversation["id"], "will this survive?"))
    assert ("janus.error" in [name for name, _ in events]) is True

    messages = client.get(f"/v1/conversations/{conversation['id']}").json()["messages"]
    assert messages[0]["content"] == "will this survive?", "the question must not be lost"
    assert messages[1]["status"] == "error"
    assert messages[1]["error"]["code"] == "all_candidates_failed"


def test_a_failed_turn_is_not_replayed_as_context(client, conversation, gateway_stub) -> None:
    gateway_stub.stream_error = UnavailableError("nope")
    send(client, conversation["id"], "first")

    gateway_stub.stream_error = None
    send(client, conversation["id"], "second")

    prompt = gateway_stub.calls[-1]["payload"]["messages"]
    assert [message["role"] for message in prompt] == ["user", "user"]


# -------------------------------------------------------------- regeneration


def test_regenerating_keeps_the_original_answer(client, conversation) -> None:
    send(client, conversation["id"], "hello")
    messages = client.get(f"/v1/conversations/{conversation['id']}").json()["messages"]
    original = messages[1]

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages/{original['id']}/regenerate"
    )
    assert response.status_code == 200

    after = client.get(f"/v1/conversations/{conversation['id']}").json()["messages"]
    assert len(after) == 3
    assert after[1]["id"] == original["id"], "the replaced answer stays in the transcript"
    assert after[2]["parent_message_id"] == original["id"]


def test_regeneration_prompts_with_the_history_before_the_answer(
    client, conversation, gateway_stub
) -> None:
    send(client, conversation["id"], "only question")
    original = client.get(f"/v1/conversations/{conversation['id']}").json()["messages"][1]

    client.post(f"/v1/conversations/{conversation['id']}/messages/{original['id']}/regenerate")

    prompt = gateway_stub.calls[-1]["payload"]["messages"]
    assert [message["role"] for message in prompt] == ["user"]
    assert prompt[0]["content"] == "only question"


def test_regeneration_can_choose_a_different_model(client, conversation, gateway_stub) -> None:
    send(client, conversation["id"], "hello")
    original = client.get(f"/v1/conversations/{conversation['id']}").json()["messages"][1]

    client.post(
        f"/v1/conversations/{conversation['id']}/messages/{original['id']}/regenerate",
        params={"model": "janus/mock-reasoning"},
    )

    assert gateway_stub.calls[-1]["payload"]["model"] == "janus/mock-reasoning"


def test_a_user_message_cannot_be_regenerated(client, conversation) -> None:
    send(client, conversation["id"], "hello")
    question = client.get(f"/v1/conversations/{conversation['id']}").json()["messages"][0]

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages/{question['id']}/regenerate"
    )

    assert response.status_code == 400
    assert response.json()["error"]["param"] == "message_id"


# --------------------------------------------------------------- the runner
# Cancellation and disconnects need control over the timing of the stream, which
# a synchronous test client cannot express. These drive the runner directly.


class ScriptedGateway:
    """Yields frames on demand, so a test can act between them."""

    def __init__(self, frames: list[bytes], *, pause_before: int | None = None) -> None:
        self._frames = frames
        self._pause_before = pause_before
        self.released = asyncio.Event()

    async def stream_chat_completion(self, payload, **kwargs):
        for index, frame in enumerate(self._frames):
            if index == self._pause_before:
                await self.released.wait()
            yield frame


def turn_for(conversation_id: str, organization_id: str, user_id: str, message_id: str) -> Turn:
    return Turn(
        conversation_id=conversation_id,
        organization_id=organization_id,
        user_id=user_id,
        actor_id=user_id,
        assistant_message_id=message_id,
        sequence=2,
    )


async def _seed_turn(db, organization_id: str, user_id: str) -> tuple[str, str]:
    """A conversation with a question and a streaming assistant row."""
    service = ConversationService()
    async with db.session(organization_id=organization_id, user_id=user_id) as session:
        conversation = await service.create(
            session, organization_id=organization_id, user_id=user_id
        )
        await service.append_user_message(session, conversation, text="tell me a story")
        assistant = await service.start_assistant_message(session, conversation)
        return conversation.id, assistant.id


@pytest.fixture
async def seeded(db, client, registered_user):
    organization_id = registered_user["organization"]["id"]
    user_id = registered_user["user"]["id"]
    conversation_id, message_id = await _seed_turn(db, organization_id, user_id)
    return {
        "organization_id": organization_id,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }


async def _stored_message(db, seeded, message_id: str) -> Message:
    async with db.session(
        organization_id=seeded["organization_id"], user_id=seeded["user_id"]
    ) as session:
        message = await session.get(Message, message_id)
        assert message is not None
        return message


async def test_cancelling_keeps_the_words_that_arrived(db, seeded) -> None:
    frames = [
        b'event: janus.routing\ndata: {"model":"janus/mock-small","deployment":"d",'
        b'"provider":"janus","privacy":"local"}\n\n',
        b'data: {"id":"c","object":"chat.completion.chunk","created":0,"model":"m",'
        b'"choices":[{"index":0,"delta":{"content":"Once upon"}}]}\n\n',
        b'data: {"id":"c","object":"chat.completion.chunk","created":0,"model":"m",'
        b'"choices":[{"index":0,"delta":{"content":" a time"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    # Held after the first content frame, so the cancel lands mid-answer rather
    # than before one exists.
    gateway = ScriptedGateway(frames, pause_before=2)
    cancellations = CancellationRegistry()
    runner = ChatRunner(db, ConversationService(), gateway, cancellations)

    turn = turn_for(
        seeded["conversation_id"],
        seeded["organization_id"],
        seeded["user_id"],
        seeded["message_id"],
    )
    stream = runner.run(
        turn,
        prompt=[{"role": "user", "content": "tell me a story"}],
        model="auto",
        mode=ExecutionMode.AUTO,
        classification=Classification.INTERNAL,
        request_id="rq_cancel",
    )

    received: list[bytes] = []
    async for chunk in stream:
        received.append(chunk)
        if b"Once upon" in chunk:
            # The first words are out; cancel from somewhere else, as a second tab
            # would.
            cancellations.cancel(seeded["conversation_id"])

    assert any(b"janus.cancelled" in chunk for chunk in received)

    stored = await _stored_message(db, seeded, seeded["message_id"])
    assert stored.status == "cancelled"
    assert content_text(stored.content) == "Once upon"


async def test_a_client_that_disappears_still_leaves_a_finalized_turn(db, seeded) -> None:
    frames = [
        b'event: janus.routing\ndata: {"model":"janus/mock-small","deployment":"d",'
        b'"provider":"janus","privacy":"local"}\n\n',
        b'data: {"id":"c","object":"chat.completion.chunk","created":0,"model":"m",'
        b'"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    gateway = ScriptedGateway(frames)
    gateway.released.set()
    runner = ChatRunner(db, ConversationService(), gateway, CancellationRegistry())

    turn = turn_for(
        seeded["conversation_id"],
        seeded["organization_id"],
        seeded["user_id"],
        seeded["message_id"],
    )
    stream = runner.run(
        turn,
        prompt=[{"role": "user", "content": "tell me a story"}],
        model="auto",
        mode=ExecutionMode.AUTO,
        classification=Classification.INTERNAL,
        request_id="rq_disconnect",
    )

    # Read the ids and one content frame, then walk away mid-stream.
    await stream.__anext__()
    await stream.__anext__()
    await stream.__anext__()
    await stream.aclose()

    stored = await _stored_message(db, seeded, seeded["message_id"])
    assert stored.status == "cancelled", "an abandoned stream must not stay 'streaming' forever"
    assert content_text(stored.content) == "partial"


# ------------------------------------------------------- database guarantees


async def test_concurrent_sends_get_distinct_sequences(db, client, registered_user) -> None:
    """Two tabs, one conversation, no collision.

    The unique constraint on ``(conversation_id, sequence)`` is what would fail if
    sequences were allocated with ``max(sequence) + 1``.
    """
    organization_id = registered_user["organization"]["id"]
    user_id = registered_user["user"]["id"]
    service = ConversationService()

    async with db.session(organization_id=organization_id, user_id=user_id) as session:
        conversation = await service.create(
            session, organization_id=organization_id, user_id=user_id
        )
        conversation_id = conversation.id

    async def write(text: str) -> int:
        async with db.session(organization_id=organization_id, user_id=user_id) as session:
            loaded = await service.get(session, conversation_id, user_id=user_id)
            message = await service.append_user_message(session, loaded, text=text)
            return message.sequence

    sequences = await asyncio.gather(*(write(f"message {index}") for index in range(5)))

    assert sorted(sequences) == [1, 2, 3, 4, 5]


async def test_a_finalized_message_cannot_be_rewritten(db, seeded) -> None:
    """The immutability trigger, verified against the database itself."""
    service = ConversationService()
    async with db.session(
        organization_id=seeded["organization_id"], user_id=seeded["user_id"]
    ) as session:
        await service.finalize_assistant_message(
            session, seeded["message_id"], text="final answer", status="complete"
        )

    with pytest.raises(Exception, match="finalized"):
        async with db.session(
            organization_id=seeded["organization_id"], user_id=seeded["user_id"]
        ) as session:
            message = await session.get(Message, seeded["message_id"])
            assert message is not None
            message.content = [{"type": "text", "text": "tampered"}]
            await session.flush()


async def test_finalizing_twice_does_not_overwrite_the_answer(db, seeded) -> None:
    service = ConversationService()
    async with db.session(
        organization_id=seeded["organization_id"], user_id=seeded["user_id"]
    ) as session:
        await service.finalize_assistant_message(
            session, seeded["message_id"], text="first", status="complete"
        )
    async with db.session(
        organization_id=seeded["organization_id"], user_id=seeded["user_id"]
    ) as session:
        await service.finalize_assistant_message(
            session, seeded["message_id"], text="second", status="cancelled"
        )

    stored = await _stored_message(db, seeded, seeded["message_id"])
    assert content_text(stored.content) == "first"
    assert stored.status == "complete"


async def test_another_users_conversation_is_not_visible(db, client, registered_user) -> None:
    """Conversations are personal, not merely tenant-scoped."""
    organization_id = registered_user["organization"]["id"]
    owner_id = registered_user["user"]["id"]
    service = ConversationService()

    async with db.session(organization_id=organization_id, user_id=owner_id) as session:
        conversation = await service.create(
            session, organization_id=organization_id, user_id=owner_id
        )
        conversation_id = conversation.id

    async with db.session(organization_id=organization_id, user_id="usr_colleague") as session:
        page = await service.list_recent(session, user_id="usr_colleague")
        assert page.conversations == []

        with pytest.raises(Exception, match="not found"):
            await service.get(session, conversation_id, user_id="usr_colleague")


async def test_conversations_are_invisible_across_organizations(db, registered_user) -> None:
    organization_id = registered_user["organization"]["id"]
    user_id = registered_user["user"]["id"]
    service = ConversationService()

    async with db.session(organization_id=organization_id, user_id=user_id) as session:
        await service.create(session, organization_id=organization_id, user_id=user_id)

    async with db.session(organization_id="org_someone_else", user_id=user_id) as session:
        rows = await session.scalars(select(Conversation))
        assert rows.all() == []
