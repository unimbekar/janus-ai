"""Control plane application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from janus_core.errors import JanusError, ValidationError
from janus_core.ids import IdPrefix, new_id
from janus_core.logging import bind_organization_id, bind_request_id, configure_logging, get_logger
from janus_core.telemetry import instrument_app, setup_telemetry

from api_app import __version__
from api_app.cancellation import CancellationRegistry
from api_app.conversations import ConversationService
from api_app.db import Database, create_engine
from api_app.gateway_client import GatewayClient
from api_app.identity import IdentityService
from api_app.routers import (
    agents,
    attachments,
    auth,
    conversations,
    inference,
    knowledge,
    meta,
    ops,
    organizations,
)
from api_app.security import PasswordHashing
from api_app.settings import ApiSettings, get_settings
from api_app.storage import FilesystemObjectStore

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Janus-Request-Id"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: ApiSettings = app.state.settings

    app.state.db = Database(
        create_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            statement_timeout_ms=settings.database_statement_timeout_ms,
        )
    )
    app.state.identity = IdentityService(
        PasswordHashing(
            time_cost=settings.argon2_time_cost,
            memory_cost_kib=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
        ),
        session_ttl_hours=settings.session_ttl_hours,
    )
    app.state.gateway = GatewayClient(
        settings.gateway_url,
        settings.gateway_service_token,
        timeout_seconds=settings.gateway_timeout_seconds,
        service_name=settings.service_name,
    )
    app.state.conversations = ConversationService()
    # Per-instance, which is why cancelling from another device is best-effort
    # until Phase 3 (see api_app/cancellation.py).
    app.state.cancellations = CancellationRegistry()
    app.state.object_store = FilesystemObjectStore(settings.attachment_root)

    logger.info(
        "api_started",
        extra={"version": __version__, "environment": settings.environment.value},
    )
    try:
        yield
    finally:
        await app.state.gateway.aclose()
        await app.state.db.aclose()
        logger.info("api_stopped")


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.service_name, settings.environment.value, settings.log_level)
    setup_telemetry(
        settings.service_name,
        settings.environment.value,
        settings.otel_exporter_otlp_endpoint,
        settings.otel_traces_sampler_ratio,
    )

    app = FastAPI(
        title="Janus Platform API",
        version=__version__,
        summary="Identity, organizations, and the platform surface.",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )

    @app.middleware("http")
    async def correlate(
        request: Request, call_next: Callable[[Request], Awaitable[JSONResponse]]
    ) -> JSONResponse:
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_id(IdPrefix.REQUEST)
        request.state.request_id = request_id
        bind_request_id(request_id)
        bind_organization_id(None)
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
                "request_rejected", extra={"error_code": exc.code, "path": request.url.path}
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
    app.include_router(auth.router)
    app.include_router(organizations.router)
    app.include_router(inference.router)
    app.include_router(conversations.router)
    app.include_router(attachments.router)
    app.include_router(agents.router)
    app.include_router(knowledge.router)
    app.include_router(ops.router)

    instrument_app(app)
    return app


app = create_app()
