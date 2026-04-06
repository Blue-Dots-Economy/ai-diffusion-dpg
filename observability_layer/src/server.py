"""
learning_layer/src/server.py

FastAPI server wrapping ConsoleLogger.
Port: 8004

Exposes:
  POST /emit/turn    — log a TurnEvent
  POST /emit/signal  — log a discrete signal event
  GET  /health       — liveness probe
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from console_logger import ConsoleLogger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ToolCallSchema(BaseModel):
    tool_name: str
    tool_use_id: str
    input_params: dict[str, Any]


class TrustCheckResultSchema(BaseModel):
    passed: bool
    action: str
    reason: Optional[str] = None


class TurnEventRequest(BaseModel):
    session_id: str
    response_text: str
    tool_calls: List[ToolCallSchema] = []
    trust_input_result: TrustCheckResultSchema
    trust_output_result: TrustCheckResultSchema
    model_used: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    timestamp_ms: int = 0


class SignalRequest(BaseModel):
    signal_type: str
    data: dict[str, Any] = {}


class StatusResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(learning: ConsoleLogger) -> FastAPI:
    """
    Factory that wires the ConsoleLogger instance into the FastAPI app.

    Args:
        learning: Pre-constructed ConsoleLogger instance.

    Returns:
        Configured FastAPI application.
    """
    if learning is None:
        raise ValueError("learning must not be None")

    app = FastAPI(
        title="Learning Layer Service",
        description="Observability and audit logging service for the DPG AI framework.",
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @app.post("/emit/turn")
    def emit_turn(request: TurnEventRequest) -> StatusResponse:
        """Log turn-level observability data."""
        start = time.time()
        session_id = request.session_id

        try:
            # Build a dict compatible with ConsoleLogger.emit_turn()
            # ConsoleLogger accepts both dataclasses and plain dicts via _get()
            event_dict = {
                "session_id": session_id,
                "response_text": request.response_text,
                "tool_calls": [
                    {
                        "tool_name": tc.tool_name,
                        "tool_use_id": tc.tool_use_id,
                        "input_params": tc.input_params,
                    }
                    for tc in request.tool_calls
                ],
                "trust_input_result": {
                    "passed": request.trust_input_result.passed,
                    "action": request.trust_input_result.action,
                    "reason": request.trust_input_result.reason,
                },
                "trust_output_result": {
                    "passed": request.trust_output_result.passed,
                    "action": request.trust_output_result.action,
                    "reason": request.trust_output_result.reason,
                },
                "model_used": request.model_used,
                "input_tokens": request.input_tokens,
                "output_tokens": request.output_tokens,
                "latency_ms": request.latency_ms,
                "timestamp_ms": request.timestamp_ms,
            }

            learning.emit_turn(event_dict)

            logger.info(
                "learning_server.emit_turn",
                extra={
                    "operation": "server.emit_turn",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except Exception as e:
            logger.error(
                "learning_server.emit_turn_error",
                extra={
                    "operation": "server.emit_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        return StatusResponse(status="ok")

    @app.post("/emit/signal")
    def emit_signal(request: SignalRequest) -> StatusResponse:
        """Log a discrete signal event."""
        start = time.time()

        try:
            learning.emit_signal(request.signal_type, request.data)

            logger.info(
                "learning_server.emit_signal",
                extra={
                    "operation": "server.emit_signal",
                    "status": "success",
                    "signal_type": request.signal_type,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except Exception as e:
            logger.error(
                "learning_server.emit_signal_error",
                extra={
                    "operation": "server.emit_signal",
                    "status": "failure",
                    "signal_type": request.signal_type,
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
