# Stage 1: Build React frontend
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build
# After build, static files are at /app/dev_kit/agent/static (due to outDir: '../dev_kit/agent/static')

# Stage 2: Python backend with built frontend
FROM python:3.13-slim
WORKDIR /app

# Install Docker CLI (for docker compose deploy from within the container)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list && \
    apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy dev-kit package
COPY . .

# Copy built frontend static files into the correct location
COPY --from=frontend /app/dev_kit/agent/static ./dev_kit/agent/static

# Install Python deps
RUN pip install -e . --no-cache-dir

EXPOSE 8080
CMD ["uvicorn", "dev_kit.agent.app:app", "--host", "0.0.0.0", "--port", "8080"]
