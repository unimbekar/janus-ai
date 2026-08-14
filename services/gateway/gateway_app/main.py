"""Gateway application factory."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from janus_core.errors import JanusError, ValidationError
from janus_core.ids import IdPrefix, new_id
from janus_core.logging import bind_organization_id, bind_request_id, configure_logging, get_logger
from janus_core.telemetry import instrument_app, setup_telemetry

from gateway_app import __version__
from gateway_app.auth import ApiKeyAuthenticator
from gateway_app.backends import BackendRegistry
from gateway_app.db import GatewayDatabase, create_engine
from gateway_app.execution import Executor
from gateway_app.health import HealthTracker, health_probe_loop, probe_once
from gateway_app.rate_limit import RateLimiter
from gateway_app.redis_client import RedisClient
from gateway_app.registry.service import RegistryService
from gateway_app.router.resolver import ModelResolver
from gateway_app.routers import chat, embeddings, meta, models
from gateway_app.settings import GatewaySettings, get_settings
from gateway_app.telemetry.writer import TelemetryWriter

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Janus-Request-Id"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: GatewaySettings = app.state.settings

    app.state.registry_service = RegistryService(settings.registry_dir, settings.environment.value)
    app.state.backends = BackendRegistry(
        connect_timeout=settings.connect_timeout_seconds,
        request_timeout=settings.request_timeout_seconds,
    )
    app.state.health = HealthTracker(failure_threshold=settings.unhealthy_failure_threshold)
    app.state.health.seed(app.state.registry_service.current)
    app.state.resolver = ModelResolver(app.state.health)
    app.state.redis = RedisClient(settings.redis_url or None)
    app.state.rate_limiter = RateLimiter(
        app.state.redis, limit_per_minute=settings.rate_limit_per_minute
    )

    db: GatewayDatabase | None = None
    telemetry = TelemetryWriter(None)
    if settings.database_url:
        db = GatewayDatabase(create_engine(settings.database_url))
        app.state.gateway_db = db
        telemetry = TelemetryWriter(db)
        app.state.api_key_auth = ApiKeyAuthenticator(db)
    else:
        app.state.gateway_db = None
        app.state.api_key_auth = None

    app.state.executor = Executor(
        app.state.backends,
        app.state.health,
        max_attempts=settings.max_fallback_attempts,
        telemetry=telemetry,
    )

    probe_task: asyncio.Task[None] | None = None
    if settings.health_probe_enabled:
        # One synchronous pass first, so the instance does not accept traffic
        # while believing an unreachable deployment is ready.
        await probe_once(app.state.registry_service.current, app.state.backends, app.state.health)
        probe_task = asyncio.create_task(
            health_probe_loop(
                app.state.registry_service,
                app.state.backends,
                app.state.health,
                settings.health_probe_interval_seconds,
            )
        )

    logger.info(
        "gateway_started",
        extra={
            "version": __version__,
            "environment": settings.environment.value,
            "models": len(app.state.registry_service.current.models),
            "backends": app.state.backends.known_backends(),
        },
    )

    try:
        yield
    finally:
        if probe_task is not None:
            probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await probe_task
        await app.state.backends.aclose()
        if getattr(app.state, "gateway_db", None) is not None:
            await app.state.gateway_db.aclose()
        await app.state.redis.aclose()
        logger.info("gateway_stopped")


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.service_name, settings.environment.value, settings.log_level)
    setup_telemetry(
        settings.service_name,
        settings.environment.value,
        settings.otel_exporter_otlp_endpoint,
        settings.otel_traces_sampler_ratio,
    )

    app = FastAPI(
        title="Janus Model Gateway",
        version=__version__,
        summary="The single path to inference: OpenAI-compatible, provider-independent.",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def correlate(
        request: Request, call_next: Callable[[Request], Awaitable[JSONResponse]]
    ) -> JSONResponse:
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_id(IdPrefix.REQUEST)
        request.state.request_id = request_id
        bind_request_id(request_id)
        bind_organization_id(request.headers.get("X-Janus-Organization-Id"))
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(JanusError)
    async def janus_error_handler(request: Request, exc: JanusError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        if exc.http_status >= 500:
            logger.error(
                "request_failed",
                extra={"error_code": exc.code, "path": request.url.path},
                exc_info=exc,
            )
        else:
            logger.info(
                "request_rejected",
                extra={"error_code": exc.code, "path": request.url.path},
            )
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload(request_id))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        param = ".".join(str(part) for part in first.get("loc", ())[1:]) or None
        error = ValidationError(
            first.get("msg", "The request is not valid."),
            param=param,
            details={"error_count": len(exc.errors())},
        )
        return JSONResponse(
            status_code=error.http_status,
            content=error.to_payload(getattr(request.state, "request_id", None)),
        )

    app.include_router(meta.router)
    app.include_router(models.router)
    app.include_router(chat.router)
    app.include_router(embeddings.router)

    instrument_app(app)
    return app


app = create_app()
