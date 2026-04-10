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

# Overlay the compiled frontend assets from Stage 1.
# This must come after `COPY . .` so it wins over any stale
# static files that might exist in the source tree.
COPY --from=frontend /app/dev_kit/agent/static ./dev_kit/agent/static

# Install the dev-kit package in editable mode so the
# uvicorn process can import dev_kit.agent.app directly.
RUN pip install -e . --no-cache-dir

EXPOSE 8080
CMD ["uvicorn", "dev_kit.agent.app:app", "--host", "0.0.0.0", "--port", "8080"]
