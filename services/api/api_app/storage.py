"""Attachment storage.

Phase 2 stores and returns bytes; it does not parse them. That boundary is the
point: parsing user files is where document-handling vulnerabilities live, so the
extraction pipeline arrives in Phase 6 with its own review.

The interface is deliberately small — put, open, delete — because the filesystem
store used in development and the S3 store used in AWS have to be
indistinguishable to callers. Keys are always generated here; a client never
supplies one, and no key is ever a URL.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path

from janus_core.errors import JanusError, NotFoundError, ValidationError
from janus_core.logging import get_logger

logger = get_logger(__name__)

#: What Phase 2 accepts. Conservative on purpose: every type here can be stored
#: and handed back without Janus interpreting it. Office formats and archives wait
#: for the extraction pipeline that will actually open them.
ALLOWED_MIME_TYPES: dict[str, tuple[str, ...]] = {
    "text/plain": (".txt", ".text", ".log"),
    "text/markdown": (".md", ".markdown"),
    "text/csv": (".csv",),
    "application/json": (".json",),
    "application/pdf": (".pdf",),
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/gif": (".gif",),
    "image/webp": (".webp",),
}

#: Leading bytes that must be present for formats with a stable signature. A
#: declared type that contradicts the content is rejected rather than trusted,
#: because the declaration comes from the client.
_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]")
MAX_FILENAME_LENGTH = 200
READ_CHUNK_BYTES = 64 * 1024


class StorageError(JanusError):
    error_type = "internal"
    code = "attachment_storage_failed"
    http_status = 500


def safe_filename(filename: str) -> str:
    """A display name that cannot be mistaken for a path.

    The stored key is generated separately, so this only has to be safe to show
    and safe to put in a ``Content-Disposition`` header.
    """
    normalized = unicodedata.normalize("NFC", filename).strip()
    normalized = _UNSAFE_FILENAME_CHARS.sub("_", normalized).lstrip(".")
    normalized = normalized[:MAX_FILENAME_LENGTH]
    return normalized or "upload"


def validate_upload(*, filename: str, mime_type: str, data: bytes, max_bytes: int) -> str:
    """Check an upload and return the checksum. Raises on anything unacceptable.

    Four checks, in the order that fails cheapest first: size, declared type,
    extension agreement, and content signature.
    """
    if not data:
        raise ValidationError("The file is empty.", param="file")
    if len(data) > max_bytes:
        raise ValidationError(
            "The file is larger than the upload limit.",
            param="file",
            details={"limit_bytes": max_bytes, "size_bytes": len(data)},
        )

    declared = (mime_type or "").split(";")[0].strip().lower()
    if declared not in ALLOWED_MIME_TYPES:
        raise ValidationError(
            "That file type is not accepted yet.",
            param="file",
            details={"accepted": sorted(ALLOWED_MIME_TYPES)},
        )

    suffix = Path(safe_filename(filename)).suffix.lower()
    if suffix and suffix not in ALLOWED_MIME_TYPES[declared]:
        raise ValidationError(
            "The file extension does not match its type.",
            param="file",
            details={"declared": declared, "extension": suffix},
        )

    expected = _MAGIC_PREFIXES.get(declared)
    if expected and not data.startswith(expected):
        raise ValidationError(
            "The file contents do not match the declared type.",
            param="file",
            details={"declared": declared},
        )

    if declared.startswith("text/") or declared == "application/json":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                "Text files must be UTF-8.", param="file", details={"declared": declared}
            ) from exc

    return hashlib.sha256(data).hexdigest()


def storage_key(*, organization_id: str, conversation_id: str, attachment_id: str) -> str:
    """Where the bytes live. Derived only from server-generated identifiers."""
    return f"{organization_id}/{conversation_id}/{attachment_id}"


class ObjectStore(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def open(self, key: str) -> AsyncIterator[bytes]:
        """Stream the object. Raises ``NotFoundError`` if it is gone."""

    @abstractmethod
    async def delete(self, key: str) -> None: ...


class FilesystemObjectStore(ObjectStore):
    """Development and single-node storage.

    Two things this class exists to get right. Every key is validated and the
    resolved path is checked to be inside the root, so a key that tried to climb
    out with ``..`` fails instead of writing somewhere it should not. And every
    filesystem call runs in a worker thread: a 20 MB write on the event loop would
    stall every other request in the process, which is exactly the kind of stall
    that only shows up under load.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        if not _SAFE_KEY.match(key) or ".." in key.split("/"):
            raise StorageError("Invalid storage key.", details={"key_length": len(key)})
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise StorageError("Invalid storage key.", details={"key_length": len(key)})
        return path

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._put_blocking, self._path(key), data)

    @staticmethod
    def _put_blocking(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file and rename: a reader can then only ever see a
        # complete object, never a half-written one.
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except OSError as exc:
            Path(temporary).unlink(missing_ok=True)
            raise StorageError("Could not store the file.") from exc

    async def open(self, key: str) -> AsyncIterator[bytes]:
        path = self._path(key)
        try:
            handle = await asyncio.to_thread(path.open, "rb")
        except OSError as exc:
            raise NotFoundError("Attachment content not found.", code="attachment_missing") from exc

        try:
            while True:
                chunk = await asyncio.to_thread(handle.read, READ_CHUNK_BYTES)
                if not chunk:
                    return
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, True)
