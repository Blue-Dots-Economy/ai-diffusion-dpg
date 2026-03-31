# =============================================================================
# Knowledge Engine DPG — Multi-stage Dockerfile
# =============================================================================
# Stage 1 (builder): install dependencies into a venv via uv sync.
# Stage 2 (runtime): copy only the venv and source — no build tools.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Copy dependency declarations first — layer is cached unless pyproject.toml changes
COPY pyproject.toml ./

# CPU-only torch index is declared first so uv never resolves the CUDA/GPU
# variant (~2.5 GB saved). All other deps are synced in the same pass.
RUN uv sync --no-dev --no-cache --no-install-project \
    --extra-index-url https://download.pytorch.org/whl/cpu

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Non-root user for security
RUN addgroup --system knowledgeengine && adduser --system --ingroup knowledgeengine knowledgeengine

WORKDIR /app

# Copy the venv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application source, entrypoint, config, and scripts
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY main.py ./
COPY config/ ./config/

# Ownership
RUN chown -R knowledgeengine:knowledgeengine /app

USER knowledgeengine

# No API keys baked into the image.
# OPENAI_API_KEY (if needed) must be injected at runtime via environment variable.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

# ChromaDB vector store is written here at ingest time.
# Mount a named volume at this path to persist embeddings across container restarts.
VOLUME /app/chroma_db

EXPOSE 8001

CMD ["uv", "run", "python", "main.py"]
