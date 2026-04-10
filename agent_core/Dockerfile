# =============================================================================
# Agent Core DPG — Multi-stage Dockerfile
# =============================================================================
# Stage 1 (builder): install dependencies into a venv via uv sync.
# Stage 2 (runtime): copy only the venv and source — no build tools.
#
# Must be built from the repo root (so observability_layer/ is in context):
#   docker build -f agent_core/Dockerfile -t agent_core .
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy

# Copy observability_layer as a local package (pyproject.toml references ../observability_layer)
COPY observability_layer/ /observability_layer/

# Copy only the project manifest first for dependency caching
COPY agent_core/pyproject.toml agent_core/uv.lock* ./

# Sync runtime dependencies only (no dev extras); creates /app/.venv
RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Non-root user for security
RUN addgroup --system agentcore && adduser --system --ingroup agentcore agentcore

WORKDIR /app

# Copy the venv from builder (includes observability-layer and all deps)
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /observability_layer /observability_layer

# Copy application source, entrypoint, and config
COPY agent_core/src/ ./src/
COPY agent_core/main.py ./
COPY agent_core/config/ ./config/

# Ownership
RUN chown -R agentcore:agentcore /app

USER agentcore

# ANTHROPIC_API_KEY must be injected at runtime via environment variable.
# Never bake secrets into the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8000

CMD ["uv", "run", "python", "main.py"]
