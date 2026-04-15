# =============================================================================
# Reach Layer voice channel — Dockerfile
# =============================================================================
# FastAPI service on port 8006 handling inbound Vobiz voice calls.
# Pipeline: VobizOperator → SileroVAD → RayaSTT → AgentCore → RayaTTS
#
# Must be built from the repo root so reach_layer/base/ and observability_layer/
# are in the build context. Use automation/docker/docker-compose.dev.yml which
# sets the correct context automatically:
#
#   docker compose -f docker-compose.dev.yml up reach_layer_voice
# =============================================================================

FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Copy the shared reach_layer_base package and observability_layer so uv can
# resolve the local-path dependencies declared in pyproject.toml.
COPY reach_layer/base/ /reach_layer_base/
COPY observability_layer/ /observability_layer/

COPY reach_layer/voice/pyproject.toml reach_layer/voice/uv.lock* ./

# Rewrite the relative uv.sources paths to the copies above so dependency
# resolution succeeds inside the build context.
RUN sed -i 's|path = "../base"|path = "/reach_layer_base"|g; s|path = "../../observability_layer"|path = "/observability_layer"|g' pyproject.toml

RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN addgroup --system reachlayer && adduser --system --ingroup reachlayer reachlayer

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /reach_layer_base /reach_layer_base
COPY --from=builder /observability_layer /observability_layer

# src/ includes: base.py, bot.py, vobiz_adapter.py, campaign_manager.py,
#                operators/, vad/, pipecat_services/
COPY reach_layer/voice/server.py ./
COPY reach_layer/voice/src/ ./src/
# Unified Reach Layer config lives one level up, shared across cli/web/voice.
COPY reach_layer/config/ ./config/

RUN chown -R reachlayer:reachlayer /app

USER reachlayer

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
