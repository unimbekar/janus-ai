"""OpenAI API adapter."""

from __future__ import annotations

from gateway_app.backends.openai_compatible import OpenAICompatibleBackend


class OpenAIBackend(OpenAICompatibleBackend):
    backend_id = "openai"
    supports_stream_usage = True
