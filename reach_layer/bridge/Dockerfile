# =============================================================================
# Reach Layer bridge channel — Dockerfile
# =============================================================================
# Runs the OpenAI-compatible /v1/chat/completions channel as a FastAPI
# service against Agent Core.
#
# Must be built from the repo root so ../base is in the build context.
#
#   docker build -f reach_layer/bridge/Dockerfile -t reach_layer_bridge .
# =============================================================================

FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY reach_layer/base /app/reach_layer/base
COPY reach_layer/bridge/pyproject.toml /app/reach_layer/bridge/
WORKDIR /app/reach_layer/bridge
RUN uv sync --no-dev

# Copy only the runtime sources — not tests, .venv, or caches. A blanket
# `COPY reach_layer/bridge /app/reach_layer/bridge` here would clobber the
# .venv `uv sync` just built with any host-side reach_layer/bridge/.venv
# picked up by the build context (the shared .dockerignore's `.venv` /
# `.pytest_cache` patterns only match at the context root, not nested paths).
COPY reach_layer/bridge/main.py /app/reach_layer/bridge/
COPY reach_layer/bridge/src/ /app/reach_layer/bridge/src/

EXPOSE 8008
CMD ["uv", "run", "python", "main.py"]
