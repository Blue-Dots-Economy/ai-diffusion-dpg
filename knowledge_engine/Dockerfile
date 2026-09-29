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

# Pre-fetch the Chroma default ONNX embedding model so the runtime container
# does not download it on first use. Chroma expects both the archive and the
# extracted files under ~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && mkdir -p /opt/chroma-cache/onnx_models/all-MiniLM-L6-v2 \
    && curl -fsSL https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz \
         -o /opt/chroma-cache/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz \
    && tar -xzf /opt/chroma-cache/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz \
         -C /opt/chroma-cache/onnx_models/all-MiniLM-L6-v2/ \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

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

# Pre-populated Chroma ONNX model cache (skips download on first embed call).
# Chroma resolves the cache via os.path.expanduser("~/.cache/chroma"), so the
# directory must exist under the runtime user's HOME. Populate both the
# dedicated `knowledgeengine` user's HOME and `/root` so the image is
# cache-hit regardless of which user actually runs (e.g. if `USER` is
# overridden at deploy time, or the image is invoked as root).
COPY --from=builder --chown=knowledgeengine:knowledgeengine \
     /opt/chroma-cache /home/knowledgeengine/.cache/chroma
COPY --from=builder /opt/chroma-cache /root/.cache/chroma

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
    VIRTUAL_ENV=/app/.venv \
    HOME=/home/knowledgeengine

# ChromaDB vector store is written here at ingest time.
# Mount a named volume at this path to persist embeddings across container restarts.
VOLUME /app/chroma_db

EXPOSE 8001

CMD ["uv", "run", "python", "main.py"]
