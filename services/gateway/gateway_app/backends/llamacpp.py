"""llama.cpp server adapter (OpenAI-compatible)."""

from __future__ import annotations

from gateway_app.backends.openai_compatible import OpenAICompatibleBackend


class LlamaCppBackend(OpenAICompatibleBackend):
    backend_id = "llamacpp"
    supports_stream_usage = False
