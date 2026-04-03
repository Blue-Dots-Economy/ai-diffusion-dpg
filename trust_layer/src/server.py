"""
trust_layer/src/server.py

FastAPI server wrapping TrustLayer.
Port: 8003

Exposes:
  POST /check/input         — check user input against content rules
  POST /check/output        — check LLM response against output rules
  POST /check/consent       — verify consent for a write/identity connector
  POST /assemble_constraints — pre-LLM guardrail constraint assembly
  POST /consent/verify      — DPDP consent phrase evaluation
  POST /escalate            — HiTL escalation queue submission
  GET  /health              — liveness probe

Fail-closed: all endpoints return block/deny on any internal error.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI

from orchestrator import TrustLayer
from models import (
    InputCheckRequest,
    OutputCheckRequest,
    ConsentCheckRequest,
    TrustCheckResponse,
    ConsentResponse,
    StatusResponse,
    AssembleConstraintsRequest,
    GuardrailConstraints,
    ConsentVerifyRequest,
    ConsentVerifyResponse,
    HiTLEscalateRequest,
    HiTLEscalateResponse,
)

logger = logging.getLogger(__name__)


def create_app(trust: TrustLayer) -> FastAPI:
    """Factory that wires the TrustLayer instance into the FastAPI app.

    Args:
        trust: Pre-constructed TrustLayer instance.

    Returns:
        Configured FastAPI application.

    Raises:
        ValueError: If trust is None.
    """
    if trust is None:
        raise ValueError("trust must not be None")

    app = FastAPI(
        title="Trust Layer Service",
        description="Safety and compliance gate for the DPG AI framework.",
        version="0.2.0",
    )

    @app.post("/check/input")
    def check_input(request: InputCheckRequest) -> TrustCheckResponse:
        """Evaluate raw user input against content rules and topic firewall."""
        start = time.time()
        session_id = request.session_id
        try:
            result = trust.check_input(session_id, request.message, request.active_risks)
            logger.info(
                "trust_server.check_input",
                extra={
                    "operation": "server.check_input",
                    "status": "success",
                    "session_id": session_id,
                    "action": result.get("action", "allow"),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return TrustCheckResponse(
                passed=result.get("passed", False),
                action=result.get("action", "block"),
                reason=result.get("reason"),
            )
        except Exception as e:
            logger.error(
                "trust_server.check_input_error",
                extra={
                    "operation": "server.check_input",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return TrustCheckResponse(passed=False, action="block")  # fail-closed

    @app.post("/check/output")
    def check_output(request: OutputCheckRequest) -> TrustCheckResponse:
        """Evaluate LLM-generated response against output safety rules."""
        start = time.time()
        session_id = request.session_id
        try:
            result = trust.check_output(session_id, request.response)
            logger.info(
                "trust_server.check_output",
                extra={
                    "operation": "server.check_output",
                    "status": "success",
                    "session_id": session_id,
                    "action": result.get("action", "allow"),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return TrustCheckResponse(
                passed=result.get("passed", False),
                action=result.get("action", "block"),
                reason=result.get("reason"),
            )
        except Exception as e:
            logger.error(
                "trust_server.check_output_error",
                extra={
                    "operation": "server.check_output",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return TrustCheckResponse(passed=False, action="block")  # fail-closed

    @app.post("/check/consent")
    def check_consent(request: ConsentCheckRequest) -> ConsentResponse:
        """Verify that confirmed user consent exists for a write or identity connector."""
        start = time.time()
        session_id = request.session_id
        try:
            granted = trust.check_consent(session_id, request.connector_name)
            logger.info(
                "trust_server.check_consent",
                extra={
                    "operation": "server.check_consent",
                    "status": "success",
                    "session_id": session_id,
                    "connector_name": request.connector_name,
                    "granted": granted,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ConsentResponse(granted=bool(granted))
        except Exception as e:
            logger.error(
                "trust_server.check_consent_error",
                extra={
                    "operation": "server.check_consent",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ConsentResponse(granted=False)  # fail-closed

    @app.post("/assemble_constraints")
    def assemble_constraints(request: AssembleConstraintsRequest) -> GuardrailConstraints:
        """Assemble pre-LLM guardrail constraints from active risks."""
        start = time.time()
        try:
            result = trust.assemble_constraints(
                request.session_id,
                request.workflow_step,
                request.active_risks,
                request.user_segment,
            )
            logger.info(
                "trust_server.assemble_constraints",
                extra={
                    "operation": "server.assemble_constraints",
                    "status": "success",
                    "session_id": request.session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return GuardrailConstraints(**result)
        except Exception as e:
            logger.error(
                "trust_server.assemble_constraints_error",
                extra={
                    "operation": "server.assemble_constraints",
                    "status": "failure",
                    "session_id": request.session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return GuardrailConstraints()  # empty constraints on error (fail-safe)

    @app.post("/consent/verify")
    def consent_verify(request: ConsentVerifyRequest) -> ConsentVerifyResponse:
        """Evaluate user message against consent phrases."""
        start = time.time()
        try:
            granted = trust.verify_consent(request.session_id, request.user_message)
            logger.info(
                "trust_server.consent_verify",
                extra={
                    "operation": "server.consent_verify",
                    "status": "success",
                    "session_id": request.session_id,
                    "granted": granted,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ConsentVerifyResponse(granted=granted)
        except Exception as e:
            logger.error(
                "trust_server.consent_verify_error",
                extra={
                    "operation": "server.consent_verify",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ConsentVerifyResponse(granted=False)  # fail-closed

    @app.post("/escalate")
    def escalate(request: HiTLEscalateRequest) -> HiTLEscalateResponse:
        """Submit escalation event to HiTL queue."""
        start = time.time()
        try:
            result = trust.escalate(
                request.session_id,
                request.escalation_reason,
                request.user_message,
                request.workflow_step,
            )
            logger.info(
                "trust_server.escalate",
                extra={
                    "operation": "server.escalate",
                    "status": "success",
                    "session_id": request.session_id,
                    "ticket_id": result["ticket_id"],
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return HiTLEscalateResponse(**result)
        except Exception as e:
            logger.error(
                "trust_server.escalate_error",
                extra={
                    "operation": "server.escalate",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return HiTLEscalateResponse(queued=False, ticket_id="", holding_message="")

    @app.get("/health")
    def health() -> StatusResponse:
        """Liveness probe."""
        return StatusResponse(status="ok")

    return app
