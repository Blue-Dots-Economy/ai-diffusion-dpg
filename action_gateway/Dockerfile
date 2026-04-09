# =============================================================================
# Action Gateway DPG — Multi-stage Dockerfile
# =============================================================================
# Runs the mock ONEST FastAPI server on port 9999.
# In production this is replaced by a real connector to the ONEST network.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy

# Copy observability_layer (path dependency: ../observability_layer from /app = /observability_layer)
COPY observability_layer/pyproject.toml /observability_layer/pyproject.toml
COPY observability_layer/src/ /observability_layer/src/

# Copy dependency declarations — layer is cached unless pyproject.toml changes
COPY action_gateway/pyproject.toml ./

# Sync runtime dependencies only; creates /app/.venv
RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN addgroup --system actiongateway && adduser --system --ingroup actiongateway actiongateway

WORKDIR /app

# Copy the venv from builder
COPY --from=builder /app/.venv /app/.venv

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
