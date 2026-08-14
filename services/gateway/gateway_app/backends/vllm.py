"""vLLM OpenAI-compatible serving adapter."""

from __future__ import annotations

from gateway_app.backends.openai_compatible import OpenAICompatibleBackend


class VllmBackend(OpenAICompatibleBackend):
    backend_id = "vllm"
    supports_stream_usage = True
