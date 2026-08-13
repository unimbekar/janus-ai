# Multi-stage build for the control plane.
# Build context is the repository root.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/janus-core/pyproject.toml packages/janus-core/
COPY packages/janus-schemas/pyproject.toml packages/janus-schemas/
COPY services/api/pyproject.toml services/api/
COPY services/gateway/pyproject.toml services/gateway/
RUN uv sync --frozen --no-install-project --no-dev --package janus-api

COPY packages/ packages/
COPY services/api/ services/api/
RUN uv sync --frozen --no-dev --package janus-api


FROM python:3.12-slim AS runtime

RUN groupadd --system janus && useradd --system --gid janus --home /app janus

WORKDIR /app

COPY --from=builder --chown=janus:janus /app/.venv /app/.venv
COPY --from=builder --chown=janus:janus /app/packages /app/packages
COPY --from=builder --chown=janus:janus /app/services/api /app/services/api

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER janus
WORKDIR /app/services/api
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/healthz')"

# Migrations are a deliberate deploy step, not something a starting container
# does on its own: two tasks racing on the same schema is a bad way to find out.
CMD ["uvicorn", "api_app.main:app", "--host", "0.0.0.0", "--port", "8080"]
