# Multi-stage build for the Model Gateway.
# Build context is the repository root, because the service depends on workspace
# packages under packages/.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer first: application edits should not re-resolve dependencies.
COPY pyproject.toml uv.lock ./
COPY packages/janus-core/pyproject.toml packages/janus-core/
COPY packages/janus-schemas/pyproject.toml packages/janus-schemas/
COPY services/gateway/pyproject.toml services/gateway/
COPY services/api/pyproject.toml services/api/
RUN uv sync --frozen --no-install-project --no-dev --package janus-gateway

COPY packages/ packages/
COPY services/gateway/ services/gateway/
RUN uv sync --frozen --no-dev --package janus-gateway


FROM python:3.12-slim AS runtime

# Non-root by default; the container has no reason to own its own filesystem.
RUN groupadd --system janus && useradd --system --gid janus --home /app janus

WORKDIR /app

COPY --from=builder --chown=janus:janus /app/.venv /app/.venv
COPY --from=builder --chown=janus:janus /app/packages /app/packages
COPY --from=builder --chown=janus:janus /app/services/gateway /app/services/gateway
COPY --chown=janus:janus registry/ /app/registry/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JANUS_REGISTRY_DIR=/app/registry

USER janus
EXPOSE 8081

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8081/healthz')"

CMD ["uvicorn", "gateway_app.main:app", "--host", "0.0.0.0", "--port", "8081"]
