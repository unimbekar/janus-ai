"""OpenTelemetry wiring.

Optional by design: if no OTLP endpoint is configured the functions here are
no-ops, so local development and tests carry no telemetry dependency weight.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from janus_core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI

logger = get_logger(__name__)

_configured = False


def setup_telemetry(
    service_name: str,
    environment: str,
    otlp_endpoint: str | None,
    sampler_ratio: float = 1.0,
) -> None:
    """Configure the global tracer provider, if an endpoint is set."""
    global _configured
    if _configured or not otlp_endpoint:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio
    except ImportError:
        logger.warning(
            "otel_endpoint_set_but_sdk_missing",
            extra={"hint": "install janus-core[telemetry]"},
        )
        return

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": service_name, "deployment.environment": environment}
        ),
        sampler=ParentBasedTraceIdRatio(sampler_ratio),
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True
    logger.info("telemetry_configured", extra={"endpoint": otlp_endpoint})


def instrument_app(app: FastAPI) -> None:
    """Attach FastAPI instrumentation when the SDK is present."""
    if not _configured:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:  # pragma: no cover
        return
    FastAPIInstrumentor.instrument_app(app)


def current_span_attributes(**attributes: Any) -> None:
    """Attach attributes to the active span, if tracing is enabled."""
    if not _configured:
        return
    from opentelemetry import trace

    span = trace.get_current_span()
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(key, value)
