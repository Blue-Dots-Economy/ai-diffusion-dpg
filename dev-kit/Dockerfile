# ============================================================
# Stage 1: Build React frontend
# Produces compiled static assets in /app/dev_kit/agent/static.
# Kept separate so node_modules never land in the final image.
# ============================================================
FROM node:20-slim AS frontend
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
# Stage 2: Python backend + pre-built frontend assets
# Only the compiled static files are copied from Stage 1;
# Node, npm, and node_modules are discarded.
# ============================================================
FROM python:3.13-slim
WORKDIR /app

# Install Docker CLI so the Configuration Agent can run
# `docker compose` commands against the host socket at runtime.
# Uses the official Docker apt repository to ensure CLI/plugin
# versions stay in sync with the compose spec.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list && \
    apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

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
# This must come after `COPY . .` so it wins over any stale
# static files that might exist in the source tree.
COPY --from=frontend /app/dev_kit/agent/static ./dev_kit/agent/static

# Install the dev-kit package in editable mode so the
# uvicorn process can import dev_kit.agent.app directly.
RUN pip install -e . --no-cache-dir

EXPOSE 8080
CMD ["uvicorn", "dev_kit.agent.app:app", "--host", "0.0.0.0", "--port", "8080"]
