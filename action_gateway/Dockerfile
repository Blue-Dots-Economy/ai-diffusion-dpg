# =============================================================================
# Action Gateway DPG — Multi-stage Dockerfile
# =============================================================================
# Runs the mock ONEST FastAPI server on port 9999.
# In production this is replaced by a real connector to the ONEST network.
#
# Both stages are Docker Hardened Images from dhi.io, which refuses anonymous
# pulls — building needs `docker login dhi.io`.
#
# Must be built from the repo root (so observability_layer/ is in context):
#   docker build -f action_gateway/Dockerfile -t action_gateway .
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
# The DHI *dev* variant is the only one that carries a shell and a package
# manager. Its python lives under /usr/bin — the same path as in the runtime
# variant — so the venv's interpreter symlink resolves in both stages.
FROM dhi.io/python:3.14-debian13-dev AS builder

COPY --from=dhi.io/uv:0 /usr/local/bin/uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Copy observability_layer as a local package (pyproject.toml references ../observability_layer)
COPY observability_layer/ /observability_layer/

# Copy only the project manifest first for dependency caching
COPY action_gateway/pyproject.toml action_gateway/uv.lock* ./

# Sync runtime dependencies only; creates /app/.venv
RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
# Hardened runtime: no shell, no package manager, runs as the unprivileged
# `nonroot` user (uid 65532). No RUN instruction is possible past this line —
# everything arrives via COPY, and uv is not needed because the venv's python
# is on PATH.
FROM dhi.io/python:3.14-debian13 AS runtime

# USER before WORKDIR: BuildKit creates the WORKDIR owned by the current user,
# so /app itself is writable by nonroot (the old `RUN chown -R` did this, and
# a RUN is not possible here).
USER nonroot
WORKDIR /app

# Copy the venv from builder (includes observability-layer and all deps)
COPY --chown=nonroot:nonroot --from=builder /app/.venv /app/.venv
COPY --chown=nonroot:nonroot --from=builder /observability_layer /observability_layer

# Copy source package and entrypoint
# src/ must be present so `from src.mock_server import app` resolves at startup
COPY --chown=nonroot:nonroot action_gateway/src/ ./src/
COPY --chown=nonroot:nonroot action_gateway/main.py ./
COPY --chown=nonroot:nonroot action_gateway/config/ ./config/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 9999

CMD ["python", "main.py"]
