"""Prefixed, time-sortable identifiers.

Format: ``<prefix>_<26-char Crockford base32 ULID>``

The payload is a ULID: 48-bit millisecond timestamp followed by 80 bits of
randomness. Lexicographic ordering therefore matches creation order, which
keeps index locality good and makes ids readable in logs.
"""

from __future__ import annotations

import os
import time
from enum import StrEnum

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32
_ENCODED_LENGTH = 26
_TIMESTAMP_BITS = 48
_RANDOM_BYTES = 10


class IdPrefix(StrEnum):
    """Every entity that needs an external identifier."""

    ORGANIZATION = "org"
    USER = "usr"
    TEAM = "team"
    API_KEY = "key"
    SESSION = "ses"
    CONVERSATION = "cnv"
    MESSAGE = "msg"
    ATTACHMENT = "att"
    AGENT = "agt"
    AGENT_RUN = "run"
    AGENT_STEP = "stp"
    KNOWLEDGE_BASE = "kb"
    DOCUMENT = "doc"
    CHUNK = "chk"
    MODEL = "mdl"
    DEPLOYMENT = "dep"
    POLICY = "pol"
    EVAL_RUN = "evl"
    REQUEST = "rq"
    DECISION = "dec"
    AUDIT_EVENT = "aud"
    PROVIDER = "prv"
    LICENSE = "lic"
    USAGE = "usg"


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_id(prefix: IdPrefix | str) -> str:
    """Generate a new prefixed identifier.

    >>> new_id(IdPrefix.ORGANIZATION).startswith("org_")
    True
    """
    prefix_value = prefix.value if isinstance(prefix, IdPrefix) else prefix
    timestamp_ms = int(time.time() * 1000) & ((1 << _TIMESTAMP_BITS) - 1)
    randomness = int.from_bytes(os.urandom(_RANDOM_BYTES), "big")
    payload = (timestamp_ms << (_RANDOM_BYTES * 8)) | randomness
    return f"{prefix_value}_{_encode(payload, _ENCODED_LENGTH)}"


def has_prefix(value: str, prefix: IdPrefix | str) -> bool:
    """Whether ``value`` is an identifier of the given kind."""
    prefix_value = prefix.value if isinstance(prefix, IdPrefix) else prefix
    return value.startswith(f"{prefix_value}_")
