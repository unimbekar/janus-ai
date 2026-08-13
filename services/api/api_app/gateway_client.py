"""HTTP client for the Model Gateway.

The control plane never imports gateway code and never talks to a provider: it
calls this client, which speaks to the gateway over HTTP with a service token and
the policy context it has already resolved. That boundary is what keeps ADR 0001
true as the codebase grows, and it is enforced mechanically by the import-linter
contracts in the root ``pyproject.toml``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from janus_core.errors import JanusError, ProviderError, TimeoutError, UnavailableError
from janus_core.logging import get_logger
from janus_schemas.common import Classification, ExecutionMode

logger = get_logger(__name__)


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout_seconds: float = 120.0,
        service_name: str = "janus-api",
    ) -> None:
        self._service_name = service_name
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            headers={"Authorization": f"Bearer {service_token}"} if service_token else {},
        )

    def _context_headers(
        self,
        *,
        organization_id: str,
        request_id: str,
        actor_id: str | None,
        mode: ExecutionMode,
        classification: Classification,
    ) -> dict[str, str]:
        headers = {
            "X-Janus-Organization-Id": organization_id,
            "X-Janus-Request-Id": request_id,
            "X-Janus-Service": self._service_name,
            "X-Janus-Mode": mode.value,
            "X-Janus-Classification": classification.value,
        }
        if actor_id:
            headers["X-Janus-Actor-Id"] = actor_id
        return headers

    async def list_models(
        self,
        *,
        organization_id: str,
        request_id: str,
        mode: ExecutionMode,
        classification: Classification = Classification.INTERNAL,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "GET",
            "/v1/models",
            headers=self._context_headers(
                organization_id=organization_id,
                request_id=request_id,
                actor_id=actor_id,
                mode=mode,
                classification=classification,
            ),
        )
        return response.json()

    async def chat_completion(
        self,
        payload: dict[str, Any],
        *,
        organization_id: str,
        request_id: str,
        mode: ExecutionMode,
        classification: Classification,
        actor_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        response = await self._request(
            "POST",
            "/v1/chat/completions",
            json=payload,
            headers=self._context_headers(
                organization_id=organization_id,
                request_id=request_id,
                actor_id=actor_id,
                mode=mode,
                classification=classification,
            ),
        )
        return response.status_code, response.json()

    async def stream_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        organization_id: str,
        request_id: str,
        mode: ExecutionMode,
        classification: Classification,
        actor_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Relay the gateway's SSE stream through unchanged.

        Rewriting events here would mean two places define the streaming
        contract, so the control plane passes bytes along and adds nothing.
        """
        headers = self._context_headers(
            organization_id=organization_id,
            request_id=request_id,
            actor_id=actor_id,
            mode=mode,
            classification=classification,
        )
        try:
            async with self._client.stream(
                "POST", "/v1/chat/completions", json={**payload, "stream": True}, headers=headers
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise _translate_error(response.status_code, body)
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.TimeoutException as exc:
            raise TimeoutError("The model did not respond in time.") from exc
        except httpx.HTTPError as exc:
            raise UnavailableError(
                "The inference service is unavailable.", code="gateway_unreachable"
            ) from exc

    async def health(self) -> bool:
        try:
            response = await self._client.get("/healthz", timeout=httpx.Timeout(3.0, connect=2.0))
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise TimeoutError("The inference service did not respond in time.") from exc
        except httpx.HTTPError as exc:
            raise UnavailableError(
                "The inference service is unavailable.", code="gateway_unreachable"
            ) from exc

        if response.status_code >= 400:
            raise _translate_error(response.status_code, response.content)
        return response

    async def aclose(self) -> None:
        await self._client.aclose()


def _translate_error(status_code: int, body: bytes) -> JanusError:
    """Re-raise a gateway error envelope as the same typed error.

    The gateway already produced a safe, typed error; the control plane must not
    flatten it into a generic 500 and lose the code the client needs.
    """
    try:
        error = json.loads(body).get("error", {})
    except (json.JSONDecodeError, AttributeError):
        error = {}

    message = error.get("message") or "The inference request failed."
    code = error.get("code")
    details = error.get("details") or {}
    retryable = bool(error.get("retryable", False))

    for error_class in JanusError.__subclasses__():
        if error_class.http_status == status_code:
            return error_class(message, code=code, details=details, retryable=retryable)

    logger.warning("gateway_error_unmapped", extra={"status": status_code, "error_code": code})
    return ProviderError(message, code=code or "gateway_error", details=details)
