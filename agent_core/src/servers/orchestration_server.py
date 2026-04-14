"""
agent_core/src/orchestration_server.py

FastAPI server exposing the Agent Core orchestration endpoints.

Exposes:
  POST /process_turn              — receives a user turn, returns the agent response (sync, JSON).
  POST /stream_turn               — receives a user turn, returns SSE stream of events (async).
  POST /sessions/{id}/input       — submit a text segment to TurnAssembler (202).
  GET  /sessions/{id}/events      — long-lived SSE subscription for session events.
  DELETE /sessions/{id}/active_turn — interrupt the active turn (barge-in).
  GET  /health                    — liveness probe.

Session-based endpoints (#72) are only registered when a TurnAssembler is provided.
Existing endpoints remain unchanged for backward compatibility.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.orchestrator import AgentCore
from src.models import DoneEvent, SegmentInput, TurnInput, TurnResult

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
    fresh: bool = False


class ProcessTurnResponse(BaseModel):
    session_id: str
    response_text: str
    was_escalated: bool
    was_tool_used: bool
    model_used: str
    latency_ms: int


class SegmentInputRequest(BaseModel):
    """Request body for POST /sessions/{session_id}/input."""
    text: str
    user_id: str | None = None
    channel: str = "cli"
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000))


class StatusResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_orchestration_app(
    agent_core: AgentCore,
    turn_assembler: Optional[object] = None,
) -> FastAPI:
    """Factory that wires the AgentCore instance into the FastAPI app.

    Args:
        agent_core: Pre-constructed, fully-wired AgentCore instance.
        turn_assembler: Optional TurnAssembler instance. When provided, session-based
                        endpoints are registered. When None, only process_turn and
                        stream_turn are available (backward compatible with #71).

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
    # Existing endpoints (unchanged from #71)
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
            fresh=request.fresh,
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

    @app.post("/stream_turn")
    async def stream_turn(request: ProcessTurnRequest) -> StreamingResponse:
        """Execute one conversation turn with SSE streaming output.

        Returns text/event-stream with SignalEvent, SentenceEvent, and DoneEvent.
        Connection closes after DoneEvent is sent.
        """
        session_id = request.session_id
        start = time.time()

        logger.info(
            "orchestration_server.stream_turn_start",
            extra={
                "operation": "orchestration_server.stream_turn",
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
            fresh=request.fresh,
        )

        async def event_generator():
            event_count = 0
            try:
                async for event in agent_core.stream_turn(turn_input):
                    event_count += 1
                    yield event.to_sse()
            except Exception as e:
                logger.error(
                    "orchestration_server.stream_turn_error",
                    extra={
                        "operation": "orchestration_server.stream_turn",
                        "status": "failure",
                        "session_id": session_id,
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                yield DoneEvent(
                    turn_status="abandoned",
                    latency_ms=int((time.time() - start) * 1000),
                ).to_sse()
            finally:
                logger.info(
                    "orchestration_server.stream_turn_complete",
                    extra={
                        "operation": "orchestration_server.stream_turn",
                        "status": "success",
                        "session_id": session_id,
                        "event_count": event_count,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------
    # Session-based endpoints (#72 TurnAssembler)
    # Registered only when turn_assembler is provided — backward compatible.
    # ------------------------------------------------------------------

    if turn_assembler is not None:
        # Import here to avoid circular imports when turn_assembler is None
        from src.turn_assembler import TurnAssemblerBase

        _assembler: TurnAssemblerBase = turn_assembler  # type: ignore[assignment]

        @app.post("/sessions/{session_id}/input", status_code=202)
        async def session_input(session_id: str, request: SegmentInputRequest):
            """Submit a text segment to the TurnAssembler. Returns 202 immediately.

            The segment is buffered and policies are evaluated asynchronously.
            When a policy triggers, TurnAssembler calls stream_turn() directly
            and pushes events to the session's event queue.
            """
            if not request.text or not request.text.strip():
                return JSONResponse(
                    status_code=422,
                    content={"detail": "text must not be empty"},
                )

            segment = SegmentInput(
                text=request.text,
                user_id=request.user_id,
                channel=request.channel,
                timestamp_ms=request.timestamp_ms or int(time.time() * 1000),
            )

            await _assembler.add_segment(session_id, segment)

            logger.info(
                "orchestration_server.session_input",
                extra={
                    "operation": "orchestration_server.session_input",
                    "status": "success",
                    "session_id": session_id,
                },
            )
            return {"status": "accepted"}

        @app.get("/sessions/{session_id}/events")
        async def session_events(session_id: str, request: Request):
            """Long-lived SSE subscription for session events.

            Reach layer opens this connection once at session start. Events are
            yielded as they arrive from TurnAssembler's invocation pipeline.
            Closes after DoneEvent. On client disconnect, cancels the active turn.
            """
            start = time.time()

            logger.info(
                "orchestration_server.session_events_open",
                extra={
                    "operation": "orchestration_server.session_events",
                    "status": "success",
                    "session_id": session_id,
                },
            )

            async def sse_generator():
                event_count = 0
                try:
                    async for event in _assembler.subscribe(session_id):
                        event_count += 1
                        yield event.to_sse()
                except Exception as e:
                    logger.error(
                        "orchestration_server.session_events_error",
                        extra={
                            "operation": "orchestration_server.session_events",
                            "status": "failure",
                            "session_id": session_id,
                            "error": f"{type(e).__name__}: {e}",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    yield DoneEvent(turn_status="abandoned").to_sse()
                finally:
                    logger.info(
                        "orchestration_server.session_events_close",
                        extra={
                            "operation": "orchestration_server.session_events",
                            "status": "success",
                            "session_id": session_id,
                            "event_count": event_count,
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )

            return StreamingResponse(
                sse_generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        @app.delete("/sessions/{session_id}/active_turn")
        async def session_cancel(session_id: str):
            """Interrupt the active turn for a session (barge-in).

            Returns 200 if session existed, 404 if not.
            """
            # Check if session exists in the assembler
            if session_id not in _assembler._sessions:
                return JSONResponse(
                    status_code=404,
                    content={"detail": "session not found"},
                )

            await _assembler.cancel(session_id)

            logger.info(
                "orchestration_server.session_cancel",
                extra={
                    "operation": "orchestration_server.session_cancel",
                    "status": "success",
                    "session_id": session_id,
                },
            )
            return {"status": "cancelled"}

    @app.get("/health")
    def health() -> StatusResponse:
        """Liveness probe."""
        return StatusResponse(status="ok")

    return app
