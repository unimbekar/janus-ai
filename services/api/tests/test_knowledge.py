"""Knowledge bases: ingest, dedupe, retrieve, citations."""

from __future__ import annotations

from api_app.knowledge import chunk_text, hash_embed


def test_chunk_text_splits_paragraphs_and_long_lines() -> None:
    body = "First paragraph.\n\nSecond paragraph that stays together."
    assert chunk_text(body) == [
        "First paragraph.",
        "Second paragraph that stays together.",
    ]
    long = "word " * 400
    pieces = chunk_text(long)
    assert len(pieces) > 1
    assert all(len(piece) <= 800 for piece in pieces)


def test_create_ingest_and_search(client, registered_user) -> None:
    created = client.post(
        "/v1/knowledge-bases",
        json={"name": "Policies", "description": "Internal policy text"},
    )
    assert created.status_code == 201, created.text
    base = created.json()
    assert base["embedding_model"] == "janus/mock-embed"
    assert base["document_count"] == 0

    ingest = client.post(
        f"/v1/knowledge-bases/{base['id']}/documents",
        json={
            "title": "Travel",
            "content": (
                "Employees may book economy travel for domestic trips.\n\n"
                "International travel requires director approval."
            ),
        },
    )
    assert ingest.status_code == 201, ingest.text
    document = ingest.json()
    assert document["status"] == "ready"
    assert document["chunk_count"] == 2

    listed = client.get("/v1/knowledge-bases").json()
    assert listed["data"][0]["document_count"] == 1

    search = client.post(
        f"/v1/knowledge-bases/{base['id']}/search",
        json={"query": "international travel approval", "limit": 2},
    )
    assert search.status_code == 200, search.text
    hits = search.json()["data"]
    assert hits
    assert hits[0]["document_id"] == document["id"]
    assert "approval" in hits[0]["content"].lower() or "travel" in hits[0]["content"].lower()


def test_duplicate_document_is_rejected(client, registered_user) -> None:
    base = client.post("/v1/knowledge-bases", json={"name": "Dupes"}).json()
    payload = {"title": "Same", "content": "Identical body for dedupe."}
    first = client.post(f"/v1/knowledge-bases/{base['id']}/documents", json=payload)
    second = client.post(f"/v1/knowledge-bases/{base['id']}/documents", json=payload)
    assert first.status_code == 201
    assert second.status_code == 409


def test_duplicate_knowledge_base_name_is_a_conflict(client, registered_user) -> None:
    client.post("/v1/knowledge-bases", json={"name": "Shared"})
    response = client.post("/v1/knowledge-bases", json={"name": "Shared"})
    assert response.status_code == 409


def test_devanagari_is_chunked_without_splitting_aksharas(client, registered_user) -> None:
    """Script-agnostic chunking must keep a Hindi paragraph intact at this size."""
    base = client.post("/v1/knowledge-bases", json={"name": "Indic"}).json()
    hindi = "भारत एक विविधतापूर्ण देश है। यहाँ कई भाषाएँ बोली जाती हैं।"
    ingest = client.post(
        f"/v1/knowledge-bases/{base['id']}/documents",
        json={"title": "भारत", "content": hindi},
    )
    assert ingest.status_code == 201, ingest.text
    assert ingest.json()["chunk_count"] == 1

    search = client.post(f"/v1/knowledge-bases/{base['id']}/search", json={"query": "भाषाएँ"})
    assert search.status_code == 200
    assert search.json()["data"][0]["content"] == hindi


def test_hash_embed_is_stable() -> None:
    assert hash_embed("hello") == hash_embed("hello")
    assert hash_embed("hello") != hash_embed("world")
    assert len(hash_embed("hello")) == 8


def test_search_matches_a_question_to_paper_wording(client, registered_user) -> None:
    """A user question is not a hash-neighbor of the paper; lexical retrieval must win."""
    base = client.post("/v1/knowledge-bases", json={"name": "Paper"}).json()
    ingest = client.post(
        f"/v1/knowledge-bases/{base['id']}/documents",
        json={
            "title": "Paths",
            "content": (
                "Dijkstra's algorithm finds the shortest path from a single source "
                "when all edge weights are non-negative. It fails if weights can "
                "be negative; use Bellman-Ford for that case."
            ),
        },
    )
    assert ingest.status_code == 201, ingest.text
    search = client.post(
        f"/v1/knowledge-bases/{base['id']}/search",
        json={"query": "Can Dijkstra handle negative edge weights?"},
    )
    assert search.status_code == 200, search.text
    hits = search.json()["data"]
    assert hits
    blob = hits[0]["content"].lower()
    assert "dijkstra" in blob
    assert "negative" in blob


def test_chat_is_grounded_in_ingested_knowledge(client, conversation, gateway_stub) -> None:
    base = client.post("/v1/knowledge-bases", json={"name": "Grounded paper"}).json()
    client.post(
        f"/v1/knowledge-bases/{base['id']}/documents",
        json={
            "title": "Results",
            "content": (
                "The proposed transformer reaches 94.2% top-1 accuracy on ImageNet. "
                "Training used 128 TPU chips for 12 hours."
            ),
        },
    )
    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"content": "What ImageNet accuracy did the paper report?"},
    )
    assert response.status_code == 200, response.text
    assert "event: janus.citations" in response.text
    assert "94.2" in response.text

    stream_calls = [call for call in gateway_stub.calls if call["operation"] == "stream"]
    assert stream_calls
    messages = stream_calls[-1]["payload"]["messages"]
    grounded = " ".join(item["content"] for item in messages if item["role"] == "system")
    assert "94.2" in grounded
    assert "ImageNet" in grounded

    detail = client.get(f"/v1/conversations/{conversation['id']}").json()
    assistant = next(item for item in detail["messages"] if item["role"] == "assistant")
    assert assistant["citations"]
    assert "94.2" in (assistant["citations"][0]["quote"] or "")


def test_upload_multiple_files(client, registered_user) -> None:
    base = client.post("/v1/knowledge-bases", json={"name": "Files"}).json()
    response = client.post(
        f"/v1/knowledge-bases/{base['id']}/uploads",
        files=[
            ("files", ("travel.txt", b"Domestic trips are economy class.\n", "text/plain")),
            (
                "files",
                ("handbook.md", b"# Safety\n\nLock the office at 18:00 UTC.\n", "text/markdown"),
            ),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["data"]) == 2
    assert body["errors"] == []
    titles = {item["title"] for item in body["data"]}
    assert titles == {"travel.txt", "handbook.md"}
    listed = client.get("/v1/knowledge-bases").json()["data"][0]
    assert listed["document_count"] == 2

    search = client.post(
        f"/v1/knowledge-bases/{base['id']}/search",
        json={"query": "office lock time"},
    )
    assert search.status_code == 200
    assert any("18:00" in hit["content"] for hit in search.json()["data"])


def test_upload_skips_duplicates_and_rejects_unsupported(client, registered_user) -> None:
    base = client.post("/v1/knowledge-bases", json={"name": "Mixed"}).json()
    first = client.post(
        f"/v1/knowledge-bases/{base['id']}/uploads",
        files=[("files", ("same.txt", b"Identical body.\n", "text/plain"))],
    )
    assert first.status_code == 200, first.text

    mixed = client.post(
        f"/v1/knowledge-bases/{base['id']}/uploads",
        files=[
            ("files", ("same.txt", b"Identical body.\n", "text/plain")),
            ("files", ("photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png")),
            ("files", ("extra.txt", b"A second unique document.\n", "text/plain")),
        ],
    )
    assert mixed.status_code == 200, mixed.text
    body = mixed.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["title"] == "extra.txt"
    codes = {item["code"] for item in body["errors"]}
    assert "conflict" in codes
    assert "invalid_request" in codes


def test_upload_all_unsupported_is_invalid(client, registered_user) -> None:
    base = client.post("/v1/knowledge-bases", json={"name": "Nope"}).json()
    response = client.post(
        f"/v1/knowledge-bases/{base['id']}/uploads",
        files=[("files", ("photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png"))],
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"]["errors"]
