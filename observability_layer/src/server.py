"""
observability_layer/src/server.py

FastAPI server wrapping OtelObservabilityLayer.
Port: 8004

Exposes:
  POST /emit/turn      — process a TurnEvent (outcome tracking + metrics)
  POST /emit/signal    — process a discrete signal event
  GET  /validate-config — return loaded domain name (config validation check)
  GET  /health         — liveness probe

Belongs to the Observability Layer DPG block.
"""
from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from otel_observability_layer import OtelObservabilityLayer
from schema.config import ObservabilityConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
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
    turn_id: str = ""
    trace_id: str = ""
    response_text: str
    tool_calls: List[ToolCallSchema] = []
    trust_input_result: TrustCheckResultSchema
    trust_output_result: TrustCheckResultSchema
    model_used: str
    intent: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    timestamp_ms: int = 0


class SignalRequest(BaseModel):
    signal_type: str
    data: dict[str, Any] = {}


class StatusResponse(BaseModel):
    status: str


class ConfigValidationResponse(BaseModel):
    status: str
    domain: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(observability: OtelObservabilityLayer, obs_config: ObservabilityConfig) -> FastAPI:
    """Create the FastAPI application wired to OtelObservabilityLayer.

    Args:
        observability: Pre-constructed OtelObservabilityLayer instance.
        obs_config: Validated ObservabilityConfig for this domain.

    Returns:
        Configured FastAPI application.

    Raises:
        ValueError: If observability or obs_config is None.
    """
    if observability is None:
        raise ValueError("observability must not be None")
    if obs_config is None:
        raise ValueError("obs_config must not be None")

    app = FastAPI(
        title="Observability Layer Service",
        description="OpenTelemetry-compliant observability DPG block.",
        version="0.1.0",
    )

    @app.post("/emit/turn")
    def emit_turn(request: TurnEventRequest) -> StatusResponse:
        """Process a turn event — outcome tracking and OTel metrics."""
        start = time.time()
        try:
            event_dict = {
                "session_id": request.session_id,
                "turn_id": request.turn_id,
                "trace_id": request.trace_id,
                "response_text": request.response_text,
                "tool_calls": [
                    {"tool_name": tc.tool_name, "tool_use_id": tc.tool_use_id, "input_params": tc.input_params}
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
                "intent": request.intent,
                "input_tokens": request.input_tokens,
                "output_tokens": request.output_tokens,
                "latency_ms": request.latency_ms,
                "timestamp_ms": request.timestamp_ms,
            }
            observability.emit_turn(event_dict)
            logger.info(
                "observability_server.emit_turn",
                extra={
                    "operation": "server.emit_turn",
                    "status": "success",
                    "session_id": request.session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "observability_server.emit_turn_error",
                extra={
                    "operation": "server.emit_turn",
                    "status": "failure",
                    "session_id": request.session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        return StatusResponse(status="ok")

    @app.post("/emit/signal")
    def emit_signal(request: SignalRequest) -> StatusResponse:
        """Process a discrete signal event."""
        start = time.time()
        try:
            observability.emit_signal(request.signal_type, request.data)
            logger.info(
                "observability_server.emit_signal",
                extra={
                    "operation": "server.emit_signal",
                    "status": "success",
                    "signal_type": request.signal_type,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "observability_server.emit_signal_error",
                extra={
                    "operation": "server.emit_signal",
                    "status": "failure",
                    "signal_type": request.signal_type,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        return StatusResponse(status="ok")

    @app.get("/validate-config")
    def validate_config() -> ConfigValidationResponse:
        """Return the loaded domain name as a config validation check."""
        return ConfigValidationResponse(status="ok", domain=obs_config.domain)

    @app.get("/health")
    def health() -> StatusResponse:
        """Liveness probe."""
        return StatusResponse(status="ok")

    return app
