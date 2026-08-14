"""MLX local serving adapter (OpenAI-compatible HTTP front)."""

from __future__ import annotations

from gateway_app.backends.openai_compatible import OpenAICompatibleBackend


class MlxBackend(OpenAICompatibleBackend):
    backend_id = "mlx"
    supports_stream_usage = False
