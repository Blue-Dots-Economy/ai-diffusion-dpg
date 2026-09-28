# =============================================================================
# Knowledge Engine DPG — Multi-stage Dockerfile
# =============================================================================
# Stage 1 (builder): install dependencies into a venv via uv sync.
# Stage 2 (runtime): copy only the venv and source — no build tools.
#
# Both stages are Docker Hardened Images from dhi.io, which refuses anonymous
# pulls — building needs `docker login dhi.io`.
#
# Must be built from the repo root (so observability_layer/ is in context):
#   docker build -f knowledge_engine/Dockerfile -t knowledge_engine .
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
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
# Fetched with the image's own python rather than apt-installed curl: the DHI
# dev variant's apt points at the dhi.io Debian mirror, and python needs
# nothing extra.
RUN python3 -c "import os, tarfile, urllib.request; \
d = '/opt/chroma-cache/onnx_models/all-MiniLM-L6-v2'; \
os.makedirs(d, exist_ok=True); \
a = d + '/onnx.tar.gz'; \
urllib.request.urlretrieve('https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz', a); \
tarfile.open(a).extractall(d, filter='data')"

# -----------------------------------------------------------------------------
# Stage 2: runtime
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

# Copy the venv from builder (includes observability-layer and all deps)
COPY --chown=nonroot:nonroot --from=builder /app/.venv /app/.venv
COPY --chown=nonroot:nonroot --from=builder /observability_layer /observability_layer

# Pre-populated Chroma ONNX model cache (skips download on first embed call).
# Chroma resolves the cache via os.path.expanduser("~/.cache/chroma"), so the
# directory must exist under the runtime user's HOME. Populate both the
# `nonroot` user's HOME and `/root` so the image is cache-hit regardless of
# which user actually runs (docker-compose runs this service as root to write
# the root-owned chroma_data volume).
COPY --from=builder --chown=nonroot:nonroot \
     /opt/chroma-cache /home/nonroot/.cache/chroma
COPY --from=builder /opt/chroma-cache /root/.cache/chroma

# Copy application source, entrypoint, config, and scripts
COPY --chown=nonroot:nonroot knowledge_engine/src/ ./src/
COPY --chown=nonroot:nonroot knowledge_engine/scripts/ ./scripts/
COPY --chown=nonroot:nonroot knowledge_engine/main.py ./
COPY --chown=nonroot:nonroot knowledge_engine/config/ ./config/

# No API keys baked into the image.
# OPENAI_API_KEY (if needed) must be injected at runtime via environment variable.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv \
    HOME=/home/nonroot

# ChromaDB vector store is written here at ingest time.
# Mount a named volume at this path to persist embeddings across container restarts.
VOLUME /app/chroma_db

EXPOSE 8001

CMD ["python", "main.py"]
