# =============================================================================
# Reach Layer Web channel — Dockerfile
# =============================================================================
# Build the React/Vite SPA, install the FastAPI server, and serve both on
# port 8005. Direct-mode channel — each POST /chat calls Agent Core's
# synchronous /process_turn.
#
# All stages are Docker Hardened Images from dhi.io, which refuses anonymous
# pulls — building needs `docker login dhi.io`.
#
# Must be built from the repo root so reach_layer/base/ and
# observability_layer/ are in context:
#
#   docker build -f reach_layer/web/Dockerfile -t reach_layer_web .
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Node — build the React/Vite frontend
# -----------------------------------------------------------------------------
# DHI node *dev* variant (shell + npm). Build-only — only the compiled static
# assets leave this stage. Node 24 (LTS) matches signals-dpg / aggregator-dpg;
# DHI publishes no node 20. Debian rather than alpine because Vite/esbuild
# ship glibc prebuilt binaries.
FROM dhi.io/node:24-debian13-dev AS frontend

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
# The DHI *dev* variant is the only one that carries a shell and a package
# manager. Its python lives under /usr/bin — the same path as in the runtime
# variant — so the venv's interpreter symlink resolves in both stages.
FROM dhi.io/python:3.14-debian13-dev AS builder

COPY --from=dhi.io/uv:0 /usr/local/bin/uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

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

COPY --chown=nonroot:nonroot reach_layer/web/server.py ./
COPY --chown=nonroot:nonroot reach_layer/web/src/ ./src/
# Unified Reach Layer config lives one level up, shared across cli/web/voice.
COPY --chown=nonroot:nonroot reach_layer/config/ ./config/

# Copy the compiled frontend — Vite outDir is ../dist relative to web-src/,
# which resolves to /dist inside the frontend stage (WORKDIR is /build)
COPY --chown=nonroot:nonroot --from=frontend /dist ./dist/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8005

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8005"]
