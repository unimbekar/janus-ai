"""Sarvam AI via OpenAI-compatible API."""

from __future__ import annotations

from gateway_app.backends.openai_compatible import OpenAICompatibleBackend


class SarvamBackend(OpenAICompatibleBackend):
    backend_id = "sarvam"
    supports_stream_usage = False
