# =============================================================================
# Reach Layer DPG — Web UI + FastAPI server Dockerfile
# =============================================================================
# Runs the FastAPI web server (server.py) that serves the chat UI on port 8005.
# The CLI adapter (main.py) remains available for local development use.
#
# Usage (server):
#   docker run -p 8005:8005 --network dpg_net reach_layer
#
# Prerequisites: all backend services must be running and reachable on the
# Docker network before starting this container.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml ./

# Sync runtime dependencies only; creates /app/.venv
RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN addgroup --system reachlayer && adduser --system --ingroup reachlayer reachlayer

WORKDIR /app

# Copy the venv from builder
COPY --from=builder /app/.venv /app/.venv

COPY main.py server.py config_loader.py ./
COPY src/ ./src/
COPY web/ ./web/
COPY config/ ./config/

RUN chown -R reachlayer:reachlayer /app

USER reachlayer

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8005

CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8005"]
