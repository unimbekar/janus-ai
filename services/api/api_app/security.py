"""Password and token handling.

Choices worth stating, because they are the ones that get wrong by default:

  - Passwords use Argon2id with OWASP-recommended parameters. Bcrypt is not used
    because its 72-byte truncation is a footgun.
  - Session tokens and API keys are random secrets, not signed claims: they can
    be revoked instantly, which matters more here than statelessness.
  - Session tokens are stored as SHA-256 hashes; API keys are stored as Argon2id
    hashes plus a SHA-256 lookup index. Sessions are high-frequency and already
    128 bits of entropy, so a fast hash is right; API keys are long-lived
    credentials and get the slow hash.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from janus_core.errors import ValidationError

API_KEY_PREFIX = "jsk"
_SESSION_TOKEN_BYTES = 32
_API_KEY_BYTES = 32
_MIN_PASSWORD_LENGTH = 12
_MAX_PASSWORD_LENGTH = 256


class PasswordHashing:
    def __init__(
        self, *, time_cost: int = 3, memory_cost_kib: int = 65536, parallelism: int = 4
    ) -> None:
        self._hasher = PasswordHasher(
            time_cost=time_cost, memory_cost=memory_cost_kib, parallelism=parallelism
        )

    def validate(self, password: str) -> None:
        """Length-first policy: long passphrases beat composition rules."""
        if len(password) < _MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.",
                code="password_too_short",
                param="password",
            )
        if len(password) > _MAX_PASSWORD_LENGTH:
            raise ValidationError(
                "Password is too long.", code="password_too_long", param="password"
            )

    def hash(self, password: str) -> str:
        self.validate(password)
        return self._hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        """Constant-ish time verification that does not leak whether a user exists.

        A missing hash still performs a dummy verification so an SSO-only or
        unknown account does not answer faster than a real one.
        """
        if not password_hash:
            self._hasher.hash(password)
            return False
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True

    def hash_api_key(self, key: str) -> str:
        return self._hasher.hash(key)

    def verify_api_key(self, key_hash: str, key: str) -> bool:
        return self.verify(key_hash, key)


def new_session_token() -> tuple[str, str]:
    """Return ``(token, token_hash)``. Only the hash is stored."""
    token = secrets.token_urlsafe(_SESSION_TOKEN_BYTES)
    return token, hash_session_token(token)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_api_key(environment: str = "live") -> tuple[str, str, str]:
    """Return ``(key, prefix, lookup_hash)``.

    The key is shown once. ``prefix`` is safe to display in a UI; ``lookup_hash``
    indexes the key so verification is a single row fetch followed by one Argon2
    comparison.
    """
    secret = secrets.token_urlsafe(_API_KEY_BYTES)
    key = f"{API_KEY_PREFIX}_{environment}_{secret}"
    prefix = key[: len(f"{API_KEY_PREFIX}_{environment}_") + 4]
    return key, prefix, api_key_lookup_hash(key)


def api_key_lookup_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())
