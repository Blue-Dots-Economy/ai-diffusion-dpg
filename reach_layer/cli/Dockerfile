# =============================================================================
# Reach Layer CLI channel — Dockerfile
# =============================================================================
# Runs the CLI channel adapter as an interactive REPL against Agent Core.
# Intended for development and demos; production CLI use is rare.
#
# Must be built from the repo root so ../base and observability_layer are in
# the build context. Use automation/docker/docker-compose.dev.yml which sets
# the correct context automatically.
#
#   docker compose -f docker-compose.dev.yml run --rm reach_layer_cli
# =============================================================================

FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy

# Copy the shared reach_layer_base package and observability_layer so uv can
# resolve the local-path dependencies declared in pyproject.toml.
COPY reach_layer/base/ /reach_layer_base/
COPY observability_layer/ /observability_layer/

COPY reach_layer/cli/pyproject.toml reach_layer/cli/uv.lock* ./

# Rewrite the relative uv.sources paths to the copies above so dependency
# resolution succeeds inside the build context.
RUN sed -i 's|path = "../base"|path = "/reach_layer_base"|g; s|path = "../../observability_layer"|path = "/observability_layer"|g' pyproject.toml

RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN addgroup --system reachlayer && adduser --system --ingroup reachlayer reachlayer

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /reach_layer_base /reach_layer_base
COPY --from=builder /observability_layer /observability_layer

COPY reach_layer/cli/main.py ./
COPY reach_layer/cli/src/ ./src/
# Unified Reach Layer config lives one level up, shared across cli/web/voice.
COPY reach_layer/config/ ./config/

RUN chown -R reachlayer:reachlayer /app

USER reachlayer

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

# stdin must be kept open for the REPL — docker compose run --rm uses -it
CMD ["python", "main.py"]
