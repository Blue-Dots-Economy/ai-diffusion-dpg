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

# Copy dev-kit package
COPY . .

# Copy built frontend static files into the correct location
COPY --from=frontend /app/dev_kit/agent/static ./dev_kit/agent/static

# Install Python deps
RUN pip install -e . --no-cache-dir

EXPOSE 8080
CMD ["uvicorn", "dev_kit.agent.app:app", "--host", "0.0.0.0", "--port", "8080"]
