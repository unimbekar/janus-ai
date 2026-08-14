"""Google Gemini via OpenAI-compatible surface."""

from __future__ import annotations

from gateway_app.backends.openai_compatible import OpenAICompatibleBackend


class GeminiBackend(OpenAICompatibleBackend):
    backend_id = "gemini"
    supports_stream_usage = False
