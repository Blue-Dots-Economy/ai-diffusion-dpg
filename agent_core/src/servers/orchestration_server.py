"""
agent_core/src/orchestration_server.py

FastAPI server exposing the Agent Core orchestration endpoint.

Exposes:
  POST /process_turn — receives a user turn, returns the agent response.
  GET  /health       — liveness probe.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI
from pydantic import BaseModel, Field

from src.orchestrator import AgentCore
from src.models import TurnInput, TurnResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ProcessTurnRequest(BaseModel):
    session_id: str
    user_message: str
    channel: str = "cli"
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    user_id: str | None = None


class ProcessTurnResponse(BaseModel):
    session_id: str
    response_text: str
    was_escalated: bool
    was_tool_used: bool
    model_used: str
    latency_ms: int


class StatusResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_orchestration_app(agent_core: AgentCore) -> FastAPI:
    """
    Factory that wires the AgentCore instance into the FastAPI app.

    Args:
        agent_core: Pre-constructed, fully-wired AgentCore instance.

    Returns:
        Configured FastAPI application.
    """
    if agent_core is None:
        raise ValueError("agent_core must not be None")

    app = FastAPI(
        title="Agent Core Orchestration Service",
        description="Central orchestration endpoint for the DPG AI framework.",
        version="0.1.0",
    )

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass  # Observability must not prevent startup

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @app.post("/process_turn")
    def process_turn(request: ProcessTurnRequest) -> ProcessTurnResponse:
        """
        Execute one full conversation turn.

        Converts HTTP request to TurnInput, delegates to AgentCore.process_turn(),
        and converts the TurnResult to an HTTP response.
        """
        start = time.time()
        session_id = request.session_id

        logger.info(
            "orchestration_server.process_turn_start",
            extra={
                "operation": "orchestration_server.process_turn",
                "status": "success",
                "session_id": session_id,
                "channel": request.channel,
            },
        )

        turn_input = TurnInput(
            session_id=session_id,
            user_message=request.user_message,
            channel=request.channel,
            timestamp_ms=request.timestamp_ms
            if request.timestamp_ms
            else int(time.time() * 1000),
            user_id=request.user_id,
        )

        try:
            result: TurnResult = agent_core.process_turn(turn_input)

            logger.info(
                "orchestration_server.process_turn_complete",
                extra={
                    "operation": "orchestration_server.process_turn",
                    "status": "success",
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                    "was_escalated": result.was_escalated,
                    "was_tool_used": result.was_tool_used,
                },
            )

            return ProcessTurnResponse(
                session_id=result.session_id,
                response_text=result.response_text,
                was_escalated=result.was_escalated,
                was_tool_used=result.was_tool_used,
                model_used=result.model_used,
                latency_ms=result.latency_ms,
            )

        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "orchestration_server.process_turn_error",
                extra={
                    "operation": "orchestration_server.process_turn",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": latency_ms,
                },
            )
            # Return a safe structured error response rather than crashing
            return ProcessTurnResponse(
                session_id=session_id,
                response_text="I'm having trouble processing your request right now. Please try again.",
                was_escalated=False,
                was_tool_used=False,
                model_used="",
                latency_ms=latency_ms,
            )

    @app.get("/health")
    def health() -> StatusResponse:
        """Liveness probe."""
        return StatusResponse(status="ok")

    return app
