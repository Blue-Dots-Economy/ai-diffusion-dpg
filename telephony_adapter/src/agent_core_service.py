"""
telephony_adapter/src/agent_core_service.py

AgentCoreLLMService — submits each utterance to Agent Core's /process_turn HTTP API.

Agent Core is the sole LLM orchestrator in the DPG framework. This service is the
telephony adapter's bridge to Agent Core — it translates a transcript + call metadata
into a TurnInput HTTP request and returns the TurnResult. Falls back to a configured
phrase on timeout or HTTP error so the call can continue gracefully.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class AgentCoreTurnResult:
    """Normalised result from Agent Core's /process_turn endpoint."""

    session_id: str
    response_text: str
    was_escalated: bool
    was_tool_used: bool
    model_used: str
    latency_ms: int


class AgentCoreLLMService:
    """Submits transcribed utterances to Agent Core and returns the response.

    Args:
        config: Full merged config dict. Reads telephony_adapter.agent_core section.

    Raises:
        ValueError: If base_url is missing from config.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        ac_cfg = config.get("telephony_adapter", {}).get("agent_core", {})
        base_url = ac_cfg.get("base_url", "")
        if not base_url:
            raise ValueError("telephony_adapter.agent_core.base_url is required")
        self._base_url = base_url.rstrip("/")
        timeout_ms = int(ac_cfg.get("timeout_ms", 5000))
        self._timeout = timeout_ms / 1000.0
        self._fallback_phrase = ac_cfg.get(
            "fallback_phrase", "I'm sorry, I couldn't process that. Please try again."
        )

    async def process_turn(
        self,
        session_id: str,
        user_message: str,
        call_sid: str,
        caller_id: str,
    ) -> AgentCoreTurnResult:
        """Submit one utterance to Agent Core and return the response.

        Uses call_sid as the opaque user_id — never passes caller_id (phone number)
        to Agent Core to avoid PII in logs.

        On HTTP error or timeout, returns a fallback response so the call
        continues rather than hanging silently.

        Args:
            session_id: Stable session UUID for this call's lifetime.
            user_message: Transcribed text from the caller's utterance.
            call_sid: Opaque call identifier (used as user_id, not caller phone).
            caller_id: Caller phone number. Present only to satisfy signature;
                       never forwarded to Agent Core.

        Returns:
            AgentCoreTurnResult with response text and escalation flag.
        """
        from opentelemetry import trace as _otel_trace
        tracer = _otel_trace.get_tracer("telephony_adapter")
        start = time.time()
        url = f"{self._base_url}/process_turn"
        payload = {
            "session_id": session_id,
            "user_message": user_message,
            "channel": "telephony",
            "user_id": call_sid,
            "timestamp_ms": int(start * 1000),
        }

        with tracer.start_as_current_span("telephony.agent_core_call") as span:
            span.set_attribute("session_id", session_id)
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=payload)

                if response.status_code != 200:
                    span.set_attribute("status", "failure")
                    logger.error(
                        "agent_core_service.http_error",
                        extra={
                            "operation": "agent_core_service.process_turn",
                            "status": "failure",
                            "error": f"HTTP {response.status_code}",
                            "latency_ms": int((time.time() - start) * 1000),
                        },
                    )
                    return self._fallback(session_id)

                data = response.json()
                latency = int((time.time() - start) * 1000)
                span.set_attribute("status", "success")
                span.set_attribute("latency_ms", latency)
                span.set_attribute("was_escalated", data.get("was_escalated", False))
                logger.info(
                    "agent_core_service.success",
                    extra={
                        "operation": "agent_core_service.process_turn",
                        "status": "success",
                        "latency_ms": latency,
                        "was_escalated": data.get("was_escalated", False),
                    },
                )
                return AgentCoreTurnResult(
                    session_id=data.get("session_id", session_id),
                    response_text=data.get("response_text", self._fallback_phrase),
                    was_escalated=data.get("was_escalated", False),
                    was_tool_used=data.get("was_tool_used", False),
                    model_used=data.get("model_used", ""),
                    latency_ms=data.get("latency_ms", 0),
                )

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                span.set_attribute("status", "failure")
                logger.error(
                    "agent_core_service.timeout",
                    extra={
                        "operation": "agent_core_service.process_turn",
                        "status": "failure",
                        "error": f"{type(e).__name__}: {e}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return self._fallback(session_id)

    def _fallback(self, session_id: str) -> AgentCoreTurnResult:
        """Return the configured fallback response when Agent Core is unavailable.

        Args:
            session_id: The session ID for the current call.

        Returns:
            AgentCoreTurnResult with fallback_phrase as response_text.
        """
        return AgentCoreTurnResult(
            session_id=session_id,
            response_text=self._fallback_phrase,
            was_escalated=False,
            was_tool_used=False,
            model_used="",
            latency_ms=0,
        )
