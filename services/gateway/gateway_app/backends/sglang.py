"""SGLang OpenAI-compatible serving adapter."""

from __future__ import annotations

from gateway_app.backends.openai_compatible import OpenAICompatibleBackend


class SGLangBackend(OpenAICompatibleBackend):
    backend_id = "sglang"
    supports_stream_usage = True
