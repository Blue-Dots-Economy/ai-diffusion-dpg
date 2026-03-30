"""
memory_layer/src/server.py

FastAPI server for the Memory Layer DPG service.
Port: 8002

Endpoints (per design doc Section 10):
  POST   /context_bundle           — context_bundle(session_id, user_id)
  POST   /write                    — write(session_id, user_id, scope, key, value)
  POST   /flush_session            — flush_session(session_id, user_id, end_reason)
  GET    /sessions/{user_id}       — get_active_sessions(user_id)
  DELETE /user/{user_id}           — delete_user(user_id)
  GET    /health                   — liveness probe
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from src.memory_layer import MemoryLayer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request schemas
# ---------------------------------------------------------------------------


class ContextBundleRequest(BaseModel):
    session_id: str
    user_id: str


class WriteRequest(BaseModel):
    session_id: str
    user_id: str
    scope: str
    key: str
    value: Any


class FlushSessionRequest(BaseModel):
    session_id: str
    user_id: str
    end_reason: str


class StatusResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(memory: MemoryLayer) -> FastAPI:
    """
    Factory that wires the MemoryLayer instance into the FastAPI app.

    Args:
        memory: Pre-constructed MemoryLayer instance (with Redis + Neo4j connected).

    Returns:
        Configured FastAPI application.
    """
    if memory is None:
        raise ValueError("memory must not be None")

    app = FastAPI(
        title="Memory Layer Service",
        description="Redis + Neo4j state management for the DPG AI framework.",
        version="2.0.0",
    )

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @app.post("/context_bundle")
    def context_bundle(request: ContextBundleRequest) -> dict:
        """
        Load full context bundle for a session.
        Returns ContextBundle as JSON (session, profile, journey).
        Returns empty bundle on any failure.
        """
        start = time.time()
        session_id = request.session_id.strip()
        user_id = request.user_id.strip()

        if not session_id or not user_id:
            return {"session": {}, "profile": {}, "journey": None}

        try:
            bundle = memory.context_bundle(session_id, user_id)
            logger.info(
                "memory_server.context_bundle",
                extra={
                    "operation": "server.context_bundle",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return bundle
        except Exception as e:
            logger.error(
                "memory_server.context_bundle_error",
                extra={
                    "operation": "server.context_bundle",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return {"session": {}, "profile": {}, "journey": None}

    @app.post("/write")
    def write(request: WriteRequest) -> StatusResponse:
        """
        Write a key/value to the appropriate backing store.
        Called asynchronously after every turn — failures are logged, never surfaced.
        """
        start = time.time()
        session_id = request.session_id.strip()
        user_id = request.user_id.strip()

        if not session_id or not user_id or not request.key:
            return StatusResponse(status="ok")

        try:
            memory.write(session_id, user_id, request.scope, request.key, request.value)
            logger.info(
                "memory_server.write",
                extra={
                    "operation": "server.write",
                    "status": "success",
                    "session_id": session_id,
                    "key": request.key,
                    "scope": request.scope,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "memory_server.write_error",
                extra={
                    "operation": "server.write",
                    "status": "failure",
                    "session_id": session_id,
                    "key": request.key,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        return StatusResponse(status="ok")

    @app.post("/flush_session")
    def flush_session(request: FlushSessionRequest) -> StatusResponse:
        """
        End a session: promote fields to Neo4j, close Journey node, clean Redis.
        """
        start = time.time()
        session_id = request.session_id.strip()
        user_id = request.user_id.strip()

        if not session_id or not user_id:
            return StatusResponse(status="ok")

        try:
            memory.flush_session(session_id, user_id, request.end_reason)
            logger.info(
                "memory_server.flush_session",
                extra={
                    "operation": "server.flush_session",
                    "status": "success",
                    "session_id": session_id,
                    "end_reason": request.end_reason,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "memory_server.flush_session_error",
                extra={
                    "operation": "server.flush_session",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        return StatusResponse(status="ok")

    @app.get("/sessions/{user_id}")
    def get_active_sessions(user_id: str) -> list[dict]:
        """
        Return active sessions for a user, sorted by last_accessed descending.
        Returns [] if no active sessions.
        """
        start = time.time()
        user_id = user_id.strip()

        if not user_id:
            return []

        try:
            sessions = memory.get_active_sessions(user_id)
            logger.info(
                "memory_server.get_active_sessions",
                extra={
                    "operation": "server.get_active_sessions",
                    "status": "success",
                    "user_id": user_id,
                    "session_count": len(sessions),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return sessions
        except Exception as e:
            logger.error(
                "memory_server.get_active_sessions_error",
                extra={
                    "operation": "server.get_active_sessions",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []

    @app.delete("/user/{user_id}")
    def delete_user(user_id: str) -> StatusResponse:
        """DPDP right-to-erasure: delete all data for this user."""
        start = time.time()
        user_id = user_id.strip()

        if not user_id:
            return StatusResponse(status="ok")

        try:
            memory.delete_user(user_id)
            logger.info(
                "memory_server.delete_user",
                extra={
                    "operation": "server.delete_user",
                    "status": "success",
                    "user_id": user_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "memory_server.delete_user_error",
                extra={
                    "operation": "server.delete_user",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        return StatusResponse(status="ok")

    @app.get("/health")
    def health() -> StatusResponse:
        """Liveness probe."""
        return StatusResponse(status="ok")

    return app
