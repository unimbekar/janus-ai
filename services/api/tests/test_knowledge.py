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
