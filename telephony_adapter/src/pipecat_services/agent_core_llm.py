"""
telephony_adapter/src/pipecat_services/agent_core_llm.py

AgentCoreLLMProcessor — Pipecat FrameProcessor that bridges TranscriptionFrames
to Agent Core's /process_turn HTTP endpoint.

Receives TranscriptionFrame from RayaSTTService, POSTs to Agent Core, then
pushes TTSSpeakFrame downstream so RayaTTSService can synthesize the response.
On was_escalated=True, also pushes EndFrame after the speak frame to close the
pipeline gracefully (VobizFrameSerializer will hang up the call on EndFrame).
On HTTP error or timeout, pushes a TTSSpeakFrame with the configured fallback
phrase so the call continues rather than hanging silently.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import time

import httpx

from pipecat.frames.frames import EndFrame, Frame, TTSSpeakFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


class AgentCoreLLMProcessor(FrameProcessor):
    """Posts each transcribed utterance to Agent Core and pushes TTS response downstream.

    Args:
        config: Full merged config dict. Reads telephony_adapter.agent_core section.
        call_sid: Opaque Vobiz call identifier.
        session_id: Stable session UUID for this call's lifetime.
        user_id: Caller E.164 phone number — stable cross-call identifier passed to
            Agent Core so the Memory Layer can recognise returning callers.

    Raises:
        ValueError: If agent_core.base_url is missing or empty.
    """

    def __init__(self, config: dict, *, call_sid: str, session_id: str, user_id: str = "") -> None:
        super().__init__()
        if config is None:
            raise ValueError("config must not be None")
        ac_cfg = config.get("telephony_adapter", {}).get("agent_core", {})
        base_url = ac_cfg.get("base_url", "").rstrip("/")
        if not base_url:
            raise ValueError(
                "telephony_adapter.agent_core.base_url is required. "
                "If running in Docker, use the service name (e.g. http://agent_core:8000). "
                "Outside Docker, use the container's published port (e.g. http://localhost:8000)."
            )
        self._base_url = base_url
        self._timeout = float(ac_cfg.get("timeout_ms", 5000)) / 1000.0
        self._fallback_phrase = ac_cfg.get(
            "fallback_phrase", "I'm sorry, I couldn't process that. Please try again."
        )
        self._call_sid = call_sid
        self._session_id = session_id
        self._user_id = user_id

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Route TranscriptionFrames to Agent Core; pass all other frames through.

        Args:
            frame: Incoming pipeline frame.
            direction: Direction of frame flow in the pipeline.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            await self._handle_transcription(frame)
        else:
            await self.push_frame(frame, direction)

    async def _handle_transcription(self, frame: TranscriptionFrame) -> None:
        """Call Agent Core and push TTSSpeakFrame (and EndFrame on escalation).

        Args:
            frame: The transcription frame containing the caller's utterance.
        """
        start = time.time()
        url = f"{self._base_url}/process_turn"
        payload = {
            "session_id": self._session_id,
            "user_message": frame.text,
            "channel": "telephony",
            "user_id": self._user_id,
            "timestamp_ms": int(start * 1000),
        }

        response_text = self._fallback_phrase
        was_escalated = False

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)

            latency_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                logger.error(
                    "agent_core_llm.http_error",
                    extra={
                        "operation": "agent_core_llm.process_turn",
                        "status": "failure",
                        "error": f"HTTP {response.status_code}",
                        "latency_ms": latency_ms,
                    },
                )
            else:
                try:
                    data = response.json()
                    response_text = data.get("response_text", self._fallback_phrase)
                    was_escalated = data.get("was_escalated", False)
                    logger.info(
                        "agent_core_llm.process_turn",
                        extra={
                            "operation": "agent_core_llm.process_turn",
                            "status": "success",
                            "latency_ms": latency_ms,
                            "was_escalated": was_escalated,
                            "was_tool_used": data.get("was_tool_used", False),
                        },
                    )
                except (ValueError, KeyError) as exc:
                    logger.error(
                        "agent_core_llm.parse_error",
                        extra={
                            "operation": "agent_core_llm.process_turn",
                            "status": "failure",
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": latency_ms,
                        },
                    )
                    # response_text stays as fallback_phrase

        except httpx.ConnectError as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "agent_core_llm.connect_error",
                extra={
                    "operation": "agent_core_llm.process_turn",
                    "status": "failure",
                    "error": f"Cannot reach agent_core at {self._base_url} — is the container running and on the same Docker network? ({exc})",
                    "latency_ms": latency_ms,
                },
            )
        except httpx.TimeoutException as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "agent_core_llm.timeout",
                extra={
                    "operation": "agent_core_llm.process_turn",
                    "status": "failure",
                    "error": f"agent_core timed out after {self._timeout:.1f}s — downstream services (memory/trust) may not be running ({type(exc).__name__})",
                    "latency_ms": latency_ms,
                },
            )

        await self.push_frame(TTSSpeakFrame(text=response_text))
        if was_escalated:
            logger.info(
                "agent_core_llm.escalated",
                extra={
                    "operation": "agent_core_llm.process_turn",
                    "status": "success",
                    "call_sid": self._call_sid,
                },
            )
            await self.push_frame(EndFrame())
