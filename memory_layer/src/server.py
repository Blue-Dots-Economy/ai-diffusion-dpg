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
from typing import Any, Optional

from fastapi import FastAPI
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from src.memory_layer import MemoryLayer

logger = logging.getLogger(__name__)


def _get_tracer() -> "otel_trace.Tracer":
    """Return the OTel tracer for the memory layer server.

    Resolved lazily so tests can install a TracerProvider before the first call.

    Returns:
        opentelemetry.trace.Tracer for this instrumentation scope.
    """
    return otel_trace.get_tracer(__name__)


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


class AuditSessionRequest(BaseModel):
    session_id: str
    user_id: str
    action: str
    reason: Optional[str] = None
    consent_given: Optional[str] = None


class AuditTurnRequest(BaseModel):
    session_id: str
    user_id: str
    turn_id: str
    user_message: str
    system_message: str
    metadata: Optional[dict] = None


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
    FastAPIInstrumentor.instrument_app(app)

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

        with _get_tracer().start_as_current_span("memory.read") as span:
            span.set_attribute("session_id", session_id)
            span.set_attribute("db.system", "redis")
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
                span.record_exception(e)
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

        with _get_tracer().start_as_current_span("memory.write") as span:
            span.set_attribute("session_id", session_id)
            span.set_attribute("db.system", "redis")
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
                span.record_exception(e)
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

    @app.post("/audit/session")
    def record_audit_session(request: AuditSessionRequest) -> StatusResponse:
        """Record session lifecycle event in SQLite."""
        start = time.time()
        try:
            memory.record_audit_session(
                session_id=request.session_id,
                user_id=request.user_id,
                action=request.action,
                reason=request.reason,
                consent_given=request.consent_given,
            )
            return StatusResponse(status="ok")
        except Exception as e:
            logger.error(
                "memory_server.audit_session_error",
                extra={
                    "operation": "server.audit_session",
                    "status": "failure",
                    "session_id": request.session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return StatusResponse(status="error")

    @app.post("/audit/turn")
    def record_audit_turn(request: AuditTurnRequest) -> StatusResponse:
        """Record a single conversation turn in SQLite."""
        start = time.time()
        try:
            memory.record_audit_turn(
                session_id=request.session_id,
                user_id=request.user_id,
                turn_id=request.turn_id,
                user_message=request.user_message,
                system_message=request.system_message,
                metadata=request.metadata,
            )
            return StatusResponse(status="ok")
        except Exception as e:
            logger.error(
                "memory_server.audit_turn_error",
                extra={
                    "operation": "server.audit_turn",
                    "status": "failure",
                    "session_id": request.session_id,
                    "turn_id": request.turn_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return StatusResponse(status="error")

    @app.get("/audit/sessions/{session_id}/history")
    def get_chat_history(session_id: str) -> list[dict]:
        """Retrieve full chat history for a session."""
        start = time.time()
        try:
            history = memory.get_chat_history(session_id)
            logger.info(
                "memory_server.get_chat_history",
                extra={
                    "operation": "server.get_chat_history",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return history
        except Exception as e:
            logger.error(
                "memory_server.get_chat_history_error",
                extra={
                    "operation": "server.get_chat_history",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return []

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

    @app.get("/users/{user_id}/active-history")
    def get_active_history(user_id: str) -> dict:
        """Return the most recent active session and its chat history for a user.

        Intended for the web UI to restore a returning user's session in a single
        call — avoids a separate session lookup and history fetch round-trip.

        Args:
            user_id: URL path parameter identifying the user.

        Returns:
            Dict with session_id (str or None) and turns (list[dict]).
            Returns {"session_id": None, "turns": []} if no active session or error.
        """
        start = time.time()
        user_id = user_id.strip()
        if not user_id:
            return {"session_id": None, "turns": []}
        try:
            result = memory.get_history_for_active_session(user_id)
            logger.info(
                "memory_server.get_active_history",
                extra={
                    "operation": "server.get_active_history",
                    "status": "success",
                    "user_id": user_id,
                    "found": result["session_id"] is not None,
                    "turn_count": len(result["turns"]),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return result
        except Exception as e:
            logger.error(
                "memory_server.get_active_history_error",
                extra={
                    "operation": "server.get_active_history",
                    "status": "failure",
                    "user_id": user_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return {"session_id": None, "turns": []}

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
