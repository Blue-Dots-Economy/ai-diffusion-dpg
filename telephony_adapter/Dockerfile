# =============================================================================
# Telephony Adapter DPG — FastAPI service Dockerfile
# =============================================================================
# Handles inbound Vobiz voice calls on port 8006.
# Pipeline: VobizOperator → SileroVAD → RayaSTT → AgentCore → RayaTTS
#
# Must be built from the repo root (so observability_layer/ is in context):
#   docker build -f telephony_adapter/Dockerfile -t telephony_adapter .
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Copy observability_layer as a local package (pyproject.toml references ../observability_layer)
COPY observability_layer/ /observability_layer/

# Copy only the project manifest first for dependency caching
COPY telephony_adapter/pyproject.toml telephony_adapter/uv.lock* ./

# Install runtime dependencies (including observability-layer local path dep)
RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN addgroup --system telephony && adduser --system --ingroup telephony telephony

WORKDIR /app

# Copy the venv from builder (includes observability-layer and all deps)
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /observability_layer /observability_layer

# Copy application source
# src/ includes: base.py, bot.py, vobiz_adapter.py, campaign_manager.py,
#                operators/, vad/, pipecat_services/
COPY telephony_adapter/server.py telephony_adapter/config_loader.py ./
COPY telephony_adapter/src/ ./src/
COPY telephony_adapter/config/ ./config/

RUN chown -R telephony:telephony /app

USER telephony

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
