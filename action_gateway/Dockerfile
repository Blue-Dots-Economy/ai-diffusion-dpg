# =============================================================================
# Action Gateway DPG — Multi-stage Dockerfile
# =============================================================================
# Runs the mock ONEST FastAPI server on port 9999.
# In production this is replaced by a real connector to the ONEST network.
#
# Must be built from the repo root (so observability_layer/ is in context):
#   docker build -f action_gateway/Dockerfile -t action_gateway .
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
COPY action_gateway/pyproject.toml action_gateway/uv.lock* ./

# Sync runtime dependencies only; creates /app/.venv
RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN addgroup --system actiongateway && adduser --system --ingroup actiongateway actiongateway

WORKDIR /app

# Copy the venv from builder (includes observability-layer and all deps)
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /observability_layer /observability_layer

# Copy source package and entrypoint
# src/ must be present so `from src.mock_server import app` resolves at startup
COPY action_gateway/src/ ./src/
COPY action_gateway/main.py ./
COPY action_gateway/config/ ./config/

RUN chown -R actiongateway:actiongateway /app

USER actiongateway

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 9999

CMD ["uv", "run", "python", "main.py"]
