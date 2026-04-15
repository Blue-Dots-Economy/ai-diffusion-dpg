# =============================================================================
# Reach Layer Web channel — Dockerfile
# =============================================================================
# Build the React/Vite SPA, install the FastAPI server, and serve both on
# port 8005. Direct-mode channel — each POST /chat calls Agent Core's
# synchronous /process_turn.
#
# Must be built from the repo root so reach_layer/base/ and
# observability_layer/ are in context:
#
#   docker build -f reach_layer/web/Dockerfile -t reach_layer_web .
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Node — build the React/Vite frontend
# -----------------------------------------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /build

COPY reach_layer/web/web-src/package.json ./
RUN npm install

COPY reach_layer/web/web-src/ ./
# Vite outputs to ../dist relative to web-src — emit directly to /dist
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

# Copy the shared reach_layer_base package and observability_layer so uv
# resolves the local-path dependencies declared in pyproject.toml.
COPY reach_layer/base/ /reach_layer_base/
COPY observability_layer/ /observability_layer/

COPY reach_layer/web/pyproject.toml reach_layer/web/uv.lock* ./

# Rewrite the relative uv.sources paths to the copies above so dependency
# resolution succeeds inside the build context.
RUN sed -i 's|path = "../base"|path = "/reach_layer_base"|g; s|path = "../../observability_layer"|path = "/observability_layer"|g' pyproject.toml

RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 3: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN addgroup --system reachlayer && adduser --system --ingroup reachlayer reachlayer

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /reach_layer_base /reach_layer_base
COPY --from=builder /observability_layer /observability_layer

COPY reach_layer/web/server.py ./
COPY reach_layer/web/src/ ./src/
# Unified Reach Layer config lives one level up, shared across cli/web/voice.
COPY reach_layer/config/ ./config/

# Copy the compiled frontend — Vite outDir is ../dist relative to web-src/,
# which resolves to /dist inside the frontend stage (WORKDIR is /build)
COPY --from=frontend /dist ./dist/

RUN chown -R reachlayer:reachlayer /app

USER reachlayer

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8005

CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8005"]
