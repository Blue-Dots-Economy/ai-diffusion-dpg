# =============================================================================
# Knowledge Engine DPG — Multi-stage Dockerfile
# =============================================================================
# Stage 1 (builder): install dependencies into a venv via uv sync.
# Stage 2 (runtime): copy only the venv and source — no build tools.
#
# Must be built from the repo root (so observability_layer/ is in context):
#   docker build -f knowledge_engine/Dockerfile -t knowledge_engine .
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy

# Copy observability_layer as a local package (pyproject.toml references ../observability_layer)
COPY observability_layer/ /observability_layer/

# Copy only the project manifest first for dependency caching
COPY knowledge_engine/pyproject.toml knowledge_engine/uv.lock* ./

# CPU-only torch index is declared first so uv never resolves the CUDA/GPU
# variant (~2.5 GB saved). All other deps are synced in the same pass.
RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Non-root user for security
RUN addgroup --system knowledgeengine && adduser --system --ingroup knowledgeengine knowledgeengine

WORKDIR /app

# Copy the venv from builder (includes observability-layer and all deps)
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /observability_layer /observability_layer

# Copy application source, entrypoint, config, and scripts
COPY knowledge_engine/src/ ./src/
COPY knowledge_engine/scripts/ ./scripts/
COPY knowledge_engine/main.py ./
COPY knowledge_engine/config/ ./config/

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
