# =============================================================================
# Reach Layer DPG — CLI variant Dockerfile
# =============================================================================
# This is the CLI (stdin/stdout) channel adapter used in the PoC.
# It is an interactive process, not a server — run with -it flags.
#
# In production the Reach Layer is replaced by a channel adapter
# (WhatsApp, Web, VOIP) that runs as a long-lived HTTP service.
#
# Usage (interactive):
#   docker run -it --network dpg_net reach_layer
#
# Prerequisites: all backend services must be running and reachable on the
# Docker network before starting this container.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml ./

# Sync runtime dependencies only; creates /app/.venv
RUN uv sync --no-dev --no-cache --no-install-project

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN addgroup --system reachlayer && adduser --system --ingroup reachlayer reachlayer

WORKDIR /app

# Copy the venv from builder
COPY --from=builder /app/.venv /app/.venv

COPY main.py ./
COPY config/ ./config/

RUN chown -R reachlayer:reachlayer /app

USER reachlayer

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

# No EXPOSE — this is a CLI client, not a server.
# stdin/stdout are the channel. Run with: docker run -it <image>

CMD ["uv", "run", "python", "main.py"]
