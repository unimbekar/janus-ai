"""Hosted and local serving adapters are registered like any other backend."""

from __future__ import annotations

import pytest
from gateway_app.backends import BackendRegistry


@pytest.mark.asyncio
async def test_hosted_and_local_adapters_are_registered() -> None:
    registry = BackendRegistry()
    try:
        for backend_id in ("vllm", "sglang", "llamacpp", "mlx", "transcription"):
            assert registry.get(backend_id).backend_id == backend_id
    finally:
        await registry.aclose()
