# All stages are Docker Hardened Images from dhi.io, which refuses
# anonymous pulls — building needs `docker login dhi.io`.
#
# ============================================================
# Stage 1: Build React frontend
# Produces compiled static assets in /app/dev_kit/agent/static.
# Kept separate so node_modules never land in the final image.
# ============================================================
# DHI node *dev* variant (shell + npm). Build-only — only the compiled static
# assets leave this stage. Node 24 (LTS) matches signals-dpg / aggregator-dpg;
# DHI publishes no node 20. Debian rather than alpine because Vite/esbuild
# ship glibc prebuilt binaries.
FROM dhi.io/node:24-debian13-dev AS frontend
WORKDIR /app/frontend

# Install dependencies before copying source so Docker can cache
# this layer and skip npm install on subsequent builds when only
# source files change.
COPY dev-kit/frontend/package*.json ./
RUN npm install

# Copy remaining source (node_modules excluded via .dockerignore).
COPY dev-kit/frontend/ ./

# vite.config.js sets outDir: '../dev_kit/agent/static', so the
# compiled assets land at /app/dev_kit/agent/static inside this stage.
RUN npm run build


# ============================================================
# Stage 2: Python build — dev-kit package + baked schemas
# The DHI *dev* variant is the only one that carries a shell
# and pip. Its python lives under /usr/bin — the same path as
# in the runtime variant — so the venv resolves in both stages.
# ============================================================
FROM dhi.io/python:3.13-debian13-dev AS builder
WORKDIR /app

# Copy the dev-kit Python package (frontend/node_modules and
# Python caches excluded via .dockerignore).
COPY dev-kit/ .

# Bake each runtime block's Pydantic config schema into the image so the
# dev-kit's pre-deploy dry-run can validate generated YAML against the
# exact same MergedConfig the runtime service will use at boot. Each schema
# is self-contained (only imports pydantic/enum/typing/__future__); a CI
# guard (planned) enforces this. See:
#   docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md §3
#   .claude/rules/runtime-devkit-sync.md
COPY agent_core/src/schema/config.py           /app/dpg_runtime_schemas/agent_core/config.py
COPY trust_layer/src/schema/config.py          /app/dpg_runtime_schemas/trust_layer/config.py
COPY knowledge_engine/src/schema/config.py     /app/dpg_runtime_schemas/knowledge_engine/config.py
COPY action_gateway/src/schema/config.py       /app/dpg_runtime_schemas/action_gateway/config.py
COPY memory_layer/src/schema/config.py         /app/dpg_runtime_schemas/memory_layer/config.py
COPY observability_layer/src/schema/config.py  /app/dpg_runtime_schemas/observability_layer/config.py
COPY reach_layer/base/schema/config.py         /app/dpg_runtime_schemas/reach_layer/config.py
RUN find /app/dpg_runtime_schemas -type d -exec touch {}/__init__.py \;

# Bake the automation directory (docker-compose + helm charts)
# into the image at /app/automation. dev_kit.agent.app reads
# COMPOSE_FILE and HELM_BASE from this location at runtime to
# render deployment previews and execute deploys.
COPY automation/ ./automation/

# Overlay the compiled frontend assets from Stage 1.
# This must come after `COPY dev-kit/ .` so it wins over any stale
# static files that might exist in the source tree.
COPY --from=frontend /app/dev_kit/agent/static ./dev_kit/agent/static

# Install the dev-kit package in editable mode into a venv so the
# uvicorn process can import dev_kit.agent.app directly. The runtime
# stage has no pip, so the finished venv is copied across; /opt/venv
# rather than /app/.venv so it cannot collide with a host-side .venv
# in the build context.
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -e .


# ============================================================
# Stage 3: Runtime
# Hardened runtime: no shell, no package manager. No RUN is
# possible past this line — everything arrives via COPY.
# ============================================================
FROM dhi.io/python:3.13-debian13

# Docker CLI + compose plugin so the Configuration Agent can run
# `docker compose` commands against the host socket at runtime.
# Taken from the DHI docker CLI image (same hardened supply chain)
# instead of apt, which the runtime variant does not have. The
# plugin path is one the docker CLI searches by default.
COPY --from=dhi.io/docker:29-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dhi.io/docker:29-cli \
     /usr/libexec/docker/cli-plugins/docker-compose \
     /usr/libexec/docker/cli-plugins/docker-compose

# Runs as root, deliberately — unchanged from the previous image. The
# dev-kit drives the host Docker daemon through the mounted
# /var/run/docker.sock (root-owned) and keeps its server key under
# /root/.devkit (the devkit_keystore volume). The runtime variant's
# default user is nonroot, so root is set explicitly, and HOME with it
# because the image ships no passwd entry to derive it from.
USER 0:0
ENV HOME=/root
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv

EXPOSE 8080
CMD ["uvicorn", "dev_kit.agent.app:app", "--host", "0.0.0.0", "--port", "8080"]
