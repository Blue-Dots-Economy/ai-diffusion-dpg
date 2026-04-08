FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy observability_layer first (local dep)
COPY observability_layer/ /observability_layer/

# Copy project files
COPY telephony_adapter/ /app/

# Install dependencies
RUN uv sync --no-dev

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
