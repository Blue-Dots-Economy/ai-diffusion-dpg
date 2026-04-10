# =============================================================================
# Trust Layer DPG — Multi-stage Dockerfile
# =============================================================================
# Stage 1 (builder): install dependencies into a venv via uv sync.
# Stage 2 (runtime): copy only the venv and source — no build tools.
#
# Must be built from the repo root (so observability_layer/ is in context):
#   docker build -f trust_layer/Dockerfile -t trust_layer .
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
COPY trust_layer/pyproject.toml trust_layer/uv.lock* ./

# Sync runtime dependencies only; creates /app/.venv
RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Non-root user for security
RUN addgroup --system trustlayer && adduser --system --ingroup trustlayer trustlayer

WORKDIR /app

# Copy the venv from builder (includes observability-layer and all deps)
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /observability_layer /observability_layer

# Copy application source, entrypoint, and config
COPY trust_layer/src/ ./src/
COPY trust_layer/main.py ./
COPY trust_layer/config/ ./config/

# Ownership
RUN chown -R trustlayer:trustlayer /app

USER trustlayer

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8003

CMD ["uv", "run", "python", "main.py"]
