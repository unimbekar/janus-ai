"""Typed error taxonomy shared by every Janus service.

The wire format is fixed by docs/api.md §7: an ``error`` object carrying a
coarse ``type``, a stable machine-readable ``code``, a safe human message,
optional ``details``, and a ``retryable`` hint.

Rules enforced here:
  - ``code`` values are stable API surface; renaming one is a breaking change.
  - ``details`` must never carry secrets, internal endpoints, other tenants'
    data, or model chain-of-thought. Callers are responsible for what they put
    in, so keep it to constraint classes and counts.
"""

from __future__ import annotations

from typing import Any


class JanusError(Exception):
    """Base class for all errors with a defined API representation."""

    error_type: str = "internal"
    code: str = "internal_error"
    http_status: int = 500
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        param: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}
        self.param = param
        if retryable is not None:
            self.retryable = retryable

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        """Render the documented error envelope."""
        return {
            "error": {
                "type": self.error_type,
                "code": self.code,
                "message": self.message,
                "param": self.param,
                "request_id": request_id,
                "details": self.details,
                "retryable": self.retryable,
            }
        }


class ValidationError(JanusError):
    error_type = "invalid_request"
    code = "invalid_request"
    http_status = 400


class AuthenticationError(JanusError):
    error_type = "authentication"
    code = "invalid_credentials"
    http_status = 401


class AuthorizationError(JanusError):
    error_type = "authorization"
    code = "insufficient_scope"
    http_status = 403


class PolicyViolationError(JanusError):
    """Request cannot be served without violating a policy constraint.

    Never raised as a way of asking the caller to retry with fewer
    restrictions: routing does not relax privacy or region constraints.
    """

    error_type = "policy_violation"
    code = "no_eligible_model"
    http_status = 403


class NotFoundError(JanusError):
    error_type = "not_found"
    code = "not_found"
    http_status = 404


class ConflictError(JanusError):
    error_type = "conflict"
    code = "conflict"
    http_status = 409


class RateLimitError(JanusError):
    error_type = "rate_limit"
    code = "org_rate_limit"
    http_status = 429
    retryable = True


class ProviderError(JanusError):
    error_type = "provider_error"
    code = "provider_bad_response"
    http_status = 502
    retryable = True


class UnavailableError(JanusError):
    error_type = "unavailable"
    code = "all_candidates_failed"
    http_status = 503
    retryable = True


class TimeoutError(JanusError):
    error_type = "timeout"
    code = "deadline_exceeded"
    http_status = 504
    retryable = True
