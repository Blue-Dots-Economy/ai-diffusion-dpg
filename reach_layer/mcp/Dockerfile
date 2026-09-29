# =============================================================================
# Reach Layer MCP channel — Dockerfile
# =============================================================================
# Runs the MCP channel adapter as a FastAPI service against Agent Core.
#
# Both stages are Docker Hardened Images from dhi.io, which refuses anonymous
# pulls — building needs `docker login dhi.io`.
#
# Must be built from the repo root so ../base and observability_layer are in
# the build context. Use automation/docker/docker-compose.dev.yml which sets
# the correct context automatically.
#
#   docker build -f reach_layer/mcp/Dockerfile -t reach_layer_mcp .
# =============================================================================

# The DHI *dev* variant is the only one that carries a shell and a package
# manager. Its python lives under /usr/bin — the same path as in the runtime
# variant — so the venv's interpreter symlink resolves in both stages.
FROM dhi.io/python:3.14-debian13-dev AS builder

COPY --from=dhi.io/uv:0 /usr/local/bin/uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Copy the shared reach_layer_base package and observability_layer so uv can
# resolve the local-path dependencies declared in pyproject.toml.
COPY reach_layer/base/ /reach_layer_base/
COPY observability_layer/ /observability_layer/

COPY reach_layer/mcp/pyproject.toml reach_layer/mcp/uv.lock* ./

# Rewrite the relative uv.sources paths to the copies above so dependency
# resolution succeeds inside the build context.
RUN sed -i 's|path = "../base"|path = "/reach_layer_base"|g; s|path = "../../observability_layer"|path = "/observability_layer"|g' pyproject.toml

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

COPY --chown=nonroot:nonroot --from=builder /app/.venv /app/.venv
COPY --chown=nonroot:nonroot --from=builder /reach_layer_base /reach_layer_base
COPY --chown=nonroot:nonroot --from=builder /observability_layer /observability_layer

COPY --chown=nonroot:nonroot reach_layer/mcp/main.py ./
COPY --chown=nonroot:nonroot reach_layer/mcp/src/ ./src/
# Unified Reach Layer config lives one level up, shared across cli/web/voice.
COPY --chown=nonroot:nonroot reach_layer/config/ ./config/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8007

CMD ["python", "main.py"]
