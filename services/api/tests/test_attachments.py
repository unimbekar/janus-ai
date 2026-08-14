"""Attachments: what is accepted, what is refused, and how it comes back.

An upload endpoint is the part of a chat product most likely to be attacked, so
these tests are mostly about refusal: wrong type, mismatched extension, lying
magic bytes, oversized bodies, someone else's file, and a path that tries to
escape the store.
"""

from __future__ import annotations

import pytest
from api_app.storage import FilesystemObjectStore, StorageError, safe_filename, validate_upload
from janus_core.errors import ValidationError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"trailer" * 8


def upload(client, conversation_id: str, name: str, content: bytes, mime: str):
    return client.post(
        f"/v1/conversations/{conversation_id}/attachments",
        files={"file": (name, content, mime)},
    )


# ------------------------------------------------------------------ the round trip


def test_a_text_file_can_be_uploaded_and_read_back(client, conversation) -> None:
    response = upload(client, conversation["id"], "notes.txt", b"hello there", "text/plain")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["filename"] == "notes.txt"
    assert body["size_bytes"] == 11
    assert body["scan_status"] == "pending", "nothing may claim a file has been scanned"

    content = client.get(f"/v1/attachments/{body['id']}/content")
    assert content.status_code == 200
    assert content.content == b"hello there"


def test_downloads_are_never_rendered_by_the_browser(client, conversation) -> None:
    """A stored file must not become stored XSS."""
    body = upload(
        client, conversation["id"], "notes.txt", b"<script>alert(1)</script>", "text/plain"
    ).json()

    response = client.get(f"/v1/attachments/{body['id']}/content")

    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


def test_sending_a_message_binds_the_attachment_to_it(client, conversation) -> None:
    uploaded = upload(client, conversation["id"], "report.pdf", PDF, "application/pdf").json()

    client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"content": "what does this say?", "attachment_ids": [uploaded["id"]]},
    )

    messages = client.get(f"/v1/conversations/{conversation['id']}").json()["messages"]
    assert [item["id"] for item in messages[0]["attachments"]] == [uploaded["id"]]


def test_an_unsent_attachment_can_be_removed(client, conversation) -> None:
    uploaded = upload(client, conversation["id"], "notes.txt", b"draft", "text/plain").json()

    assert client.delete(f"/v1/attachments/{uploaded['id']}").status_code == 204
    assert client.get(f"/v1/attachments/{uploaded['id']}/content").status_code == 404


def test_a_sent_attachment_is_part_of_the_transcript(client, conversation) -> None:
    uploaded = upload(client, conversation["id"], "notes.txt", b"sent", "text/plain").json()
    client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"content": "here it is", "attachment_ids": [uploaded["id"]]},
    )

    response = client.delete(f"/v1/attachments/{uploaded['id']}")

    assert response.status_code == 400
    assert "already been sent" in response.json()["error"]["message"]


def test_an_attachment_from_another_conversation_cannot_be_sent(client, conversation) -> None:
    other = client.post("/v1/conversations", json={}).json()
    uploaded = upload(client, other["id"], "notes.txt", b"elsewhere", "text/plain").json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"content": "borrowing this", "attachment_ids": [uploaded["id"]]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["param"] == "attachment_ids"


def test_an_attachment_cannot_be_reused_in_a_second_message(client, conversation) -> None:
    uploaded = upload(client, conversation["id"], "notes.txt", b"once", "text/plain").json()
    first = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"content": "first", "attachment_ids": [uploaded["id"]]},
    )
    assert first.status_code == 200

    second = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"content": "again", "attachment_ids": [uploaded["id"]]},
    )

    assert second.status_code == 400


def test_uploads_are_capped_per_conversation(client, conversation) -> None:
    for index in range(10):
        assert (
            upload(client, conversation["id"], f"f{index}.txt", b"x", "text/plain").status_code
            == 201
        )

    response = upload(client, conversation["id"], "eleventh.txt", b"x", "text/plain")

    assert response.status_code == 400
    assert response.json()["error"]["details"]["limit"] == 10


# --------------------------------------------------------------- validation


def test_an_unaccepted_type_is_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_upload(
            filename="payload.exe",
            mime_type="application/x-msdownload",
            data=b"MZ\x90\x00",
            max_bytes=1024,
        )

    assert "accepted" in excinfo.value.details


def test_an_extension_that_contradicts_the_type_is_refused() -> None:
    with pytest.raises(ValidationError, match="extension"):
        validate_upload(filename="image.png", mime_type="text/plain", data=b"hi", max_bytes=1024)


def test_content_that_contradicts_the_type_is_refused() -> None:
    """A .png that is not a PNG is the interesting case, not the honest one."""
    with pytest.raises(ValidationError, match="contents"):
        validate_upload(
            filename="fake.png", mime_type="image/png", data=b"not an image", max_bytes=1024
        )


def test_text_that_is_not_utf8_is_refused() -> None:
    with pytest.raises(ValidationError, match="UTF-8"):
        validate_upload(
            filename="notes.txt", mime_type="text/plain", data=b"\xff\xfe\x00bad", max_bytes=1024
        )


def test_an_oversized_file_is_refused_with_the_limit() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_upload(filename="big.txt", mime_type="text/plain", data=b"x" * 50, max_bytes=10)

    assert excinfo.value.details["limit_bytes"] == 10


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(ValidationError, match="empty"):
        validate_upload(filename="empty.txt", mime_type="text/plain", data=b"", max_bytes=10)


def test_a_valid_png_returns_its_checksum() -> None:
    checksum = validate_upload(
        filename="pixel.png", mime_type="image/png", data=PNG, max_bytes=1024
    )

    assert len(checksum) == 64


def test_an_oversized_upload_is_refused_over_http(client, conversation) -> None:
    body = upload(
        client, conversation["id"], "big.txt", b"x" * (20 * 1024 * 1024 + 1), "text/plain"
    )

    assert body.status_code == 400
    assert body.json()["error"]["param"] == "file"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("../../etc/passwd", "_.._etc_passwd"),
        ("report\x00.pdf", "report_.pdf"),
        ("with/slash.txt", "with_slash.txt"),
        (".hidden", "hidden"),
        ("", "upload"),
    ],
)
def test_filenames_cannot_look_like_paths(given, expected) -> None:
    assert safe_filename(given) == expected


def test_a_unicode_filename_survives() -> None:
    assert safe_filename("प्रतिवेदन.pdf") == "प्रतिवेदन.pdf"


# ------------------------------------------------------------------ the store


async def test_the_store_refuses_a_key_that_escapes_its_root(tmp_path) -> None:
    store = FilesystemObjectStore(tmp_path)

    for key in ("../escape", "org/../../escape", "/absolute", ""):
        with pytest.raises(StorageError):
            await store.put(key, b"nope")


async def test_the_store_round_trips_and_deletes(tmp_path) -> None:
    store = FilesystemObjectStore(tmp_path)
    await store.put("org_1/cnv_1/att_1", b"contents")

    chunks = [chunk async for chunk in store.open("org_1/cnv_1/att_1")]
    assert b"".join(chunks) == b"contents"

    await store.delete("org_1/cnv_1/att_1")
    with pytest.raises(Exception, match="not found"):
        [chunk async for chunk in store.open("org_1/cnv_1/att_1")]


async def test_stored_files_are_not_world_readable(tmp_path) -> None:
    store = FilesystemObjectStore(tmp_path)
    await store.put("org_1/cnv_1/att_1", b"private")

    mode = (tmp_path / "org_1/cnv_1/att_1").stat().st_mode

    assert mode & 0o077 == 0
