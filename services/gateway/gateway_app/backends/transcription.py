"""Speech-to-text via an OpenAI-compatible transcriptions endpoint."""

from __future__ import annotations

from gateway_app.backends.openai_compatible import OpenAICompatibleBackend


class TranscriptionBackend(OpenAICompatibleBackend):
    backend_id = "transcription"
    supports_stream_usage = False
