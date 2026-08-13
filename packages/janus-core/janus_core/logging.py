"""Structured JSON logging with request correlation.

Two hard rules, both from docs/security.md §9:
  - prompt and completion bodies are never logged;
  - secrets are never logged, so known-sensitive keys are redacted here as a
    backstop rather than relying on every call site.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("janus_request_id", default=None)
organization_id_var: ContextVar[str | None] = ContextVar("janus_organization_id", default=None)

_REDACTED = "[redacted]"
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "api_key",
        "authorization",
        "token",
        "key_hash",
        "secret",
        "credentials",
        "cookie",
        "set-cookie",
        # Content that must never reach logs
        "messages",
        "prompt",
        "completion",
        "content",
        "scratchpad",
    }
)

_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def _redact(key: str, value: Any) -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return _REDACTED
    if isinstance(value, dict):
        return {k: _redact(k, v) for k, v in value.items()}
    return value


class JsonFormatter(logging.Formatter):
    """Render records as single-line JSON with correlation fields attached."""

    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "environment": self.environment,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        organization_id = organization_id_var.get()
        if organization_id:
            payload["organization_id"] = organization_id

        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = _redact(key, value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(service: str, environment: str, level: str = "INFO") -> None:
    """Install the JSON formatter as the only root handler."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service=service, environment=environment))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn duplicates access logs in a different shape; keep ours only.
    for noisy in ("uvicorn.access", "uvicorn.error"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def bind_request_id(request_id: str | None) -> None:
    request_id_var.set(request_id)


def bind_organization_id(organization_id: str | None) -> None:
    organization_id_var.set(organization_id)
