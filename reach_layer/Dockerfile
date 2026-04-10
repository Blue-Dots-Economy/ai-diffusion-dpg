# =============================================================================
# Reach Layer DPG — Web UI + FastAPI server Dockerfile
# =============================================================================
# Runs the FastAPI web server (server.py) that serves the chat UI on port 8005.
# The CLI adapter (main.py) remains available for local development use.
#
# Must be built from the repo root (so observability_layer/ is in context):
#   docker build -f reach_layer/Dockerfile -t reach_layer .
#
# Prerequisites: all backend services must be running and reachable on the
# Docker network before starting this container.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Node — build the React/Vite frontend
# -----------------------------------------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /build

COPY reach_layer/web-src/package.json ./
RUN npm install

COPY reach_layer/web-src/ ./
# Vite outputs to ../web/dist relative to web-src — emit directly to /build/dist
# then copy into the right place in the runtime stage
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 2: Python — install runtime dependencies
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy

# Copy observability_layer as a local package (pyproject.toml references ../observability_layer)
COPY observability_layer/ /observability_layer/

# Copy only the project manifest first for dependency caching
COPY reach_layer/pyproject.toml reach_layer/uv.lock* ./

# Sync runtime dependencies only; creates /app/.venv
RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 3: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN addgroup --system reachlayer && adduser --system --ingroup reachlayer reachlayer

WORKDIR /app

# Copy the venv from builder (includes observability-layer and all deps)
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /observability_layer /observability_layer

COPY reach_layer/main.py reach_layer/server.py reach_layer/config_loader.py ./
COPY reach_layer/src/ ./src/
COPY reach_layer/config/ ./config/

# Copy the compiled frontend — Vite outDir is ../web/dist relative to web-src/,
# which resolves to /web/dist inside the frontend stage (WORKDIR is /build)
COPY --from=frontend /web/dist ./web/dist/

RUN chown -R reachlayer:reachlayer /app

USER reachlayer

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8005

CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8005"]
