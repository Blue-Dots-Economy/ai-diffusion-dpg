# =============================================================================
# Reach Layer bridge channel — Dockerfile
# =============================================================================
# Runs the OpenAI-compatible /v1/chat/completions channel as a FastAPI
# service against Agent Core.
#
# Both stages are Docker Hardened Images from dhi.io, which refuses anonymous
# pulls — building needs `docker login dhi.io`.
#
# Must be built from the repo root so ../base is in the build context.
#
#   docker build -f reach_layer/bridge/Dockerfile -t reach_layer_bridge .
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
# The DHI *dev* variant is the only one that carries a shell and a package
# manager. Its python lives under /usr/bin — the same path as in the runtime
# variant — so the venv's interpreter symlink resolves in both stages.
FROM dhi.io/python:3.13-debian13-dev AS builder

COPY --from=dhi.io/uv:0 /usr/local/bin/uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY reach_layer/base /app/reach_layer/base
COPY reach_layer/bridge/pyproject.toml /app/reach_layer/bridge/
WORKDIR /app/reach_layer/bridge
RUN uv sync --no-dev --no-cache

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
# Hardened runtime: no shell, no package manager, runs as the unprivileged
# `nonroot` user (uid 65532). No RUN instruction is possible past this line —
# everything arrives via COPY, and uv is not needed because the venv's python
# is on PATH.
FROM dhi.io/python:3.13-debian13 AS runtime

# USER before WORKDIR: BuildKit creates the WORKDIR owned by the current user.
USER nonroot
WORKDIR /app/reach_layer/bridge

# The venv installs reach-layer-base (and this project) editable, so both must
# land at the same paths they were synced at.
COPY --chown=nonroot:nonroot --from=builder /app/reach_layer/base /app/reach_layer/base
COPY --chown=nonroot:nonroot --from=builder /app/reach_layer/bridge/.venv /app/reach_layer/bridge/.venv

# Copy only the runtime sources — not tests, .venv, or caches. A blanket
# `COPY reach_layer/bridge /app/reach_layer/bridge` here would clobber the
# .venv `uv sync` just built with any host-side reach_layer/bridge/.venv
# picked up by the build context (the shared .dockerignore's `.venv` /
# `.pytest_cache` patterns only match at the context root, not nested paths).
COPY --chown=nonroot:nonroot reach_layer/bridge/pyproject.toml /app/reach_layer/bridge/
COPY --chown=nonroot:nonroot reach_layer/bridge/main.py /app/reach_layer/bridge/
COPY --chown=nonroot:nonroot reach_layer/bridge/src/ /app/reach_layer/bridge/src/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/reach_layer/bridge/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/reach_layer/bridge/.venv

EXPOSE 8008
CMD ["python", "main.py"]
