"""Resolution of credential references.

Registry files never contain secrets — they contain *references*. Phase 1
supports environment variables (local development) and files; AWS Secrets
Manager with caching and rotation arrives with the first cloud provider in
Phase 2.
"""

from __future__ import annotations

import os
from pathlib import Path

from janus_core.errors import JanusError


class CredentialResolutionError(JanusError):
    error_type = "internal"
    code = "credential_unavailable"
    http_status = 500


def resolve_credential(reference: str | None) -> str | None:
    """Resolve ``env://VAR``, ``file:///path``, or a literal-free reference.

    Returns ``None`` when no credential is required (local runtimes such as
    Ollama or vLLM inside the VPC). The resolved value is never logged.
    """
    if not reference:
        return None

    scheme, _, remainder = reference.partition("://")

    if scheme == "env":
        value = os.environ.get(remainder)
        if not value:
            raise CredentialResolutionError(
                "Provider credential is not configured.",
                details={"reference_scheme": "env", "variable": remainder},
            )
        return value

    if scheme == "file":
        path = Path(remainder)
        if not path.is_file():
            raise CredentialResolutionError(
                "Provider credential file is missing.",
                details={"reference_scheme": "file"},
            )
        return path.read_text(encoding="utf-8").strip()

    if scheme == "secretsmanager":
        raise CredentialResolutionError(
            "Secrets Manager credential resolution is not implemented yet.",
            details={"reference_scheme": scheme, "available_from_phase": 2},
        )

    raise CredentialResolutionError(
        "Unsupported credential reference scheme.",
        details={"reference_scheme": scheme},
    )
