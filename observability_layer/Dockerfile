# =============================================================================
# Observability Layer DPG — Multi-stage Dockerfile
# =============================================================================
# Stage 1 (builder): install dependencies into a venv via uv sync.
# Stage 2 (runtime): copy only the venv and source — no build tools.
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

# Copy dependency declarations — layer is cached unless pyproject.toml changes
COPY observability_layer/pyproject.toml ./

# Sync runtime dependencies only; creates /app/.venv
RUN uv sync --no-dev --no-cache --no-install-project

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

# Copy the venv from builder
COPY --chown=nonroot:nonroot --from=builder /app/.venv /app/.venv

# Copy application source, entrypoint, and config
COPY --chown=nonroot:nonroot observability_layer/src/ ./src/
COPY --chown=nonroot:nonroot observability_layer/main.py ./
COPY --chown=nonroot:nonroot observability_layer/config/ ./config/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

EXPOSE 8004

CMD ["python", "main.py"]
