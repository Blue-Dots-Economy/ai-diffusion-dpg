"""
telephony_adapter/src/telephony_adapter.py

VobizTelephonyAdapter — drives the per-call audio turn loop.

Implements TelephonyAdapterBase. Reads WebSocket messages from Vobiz,
buffers audio, calls Raya STT on utterance end (stop event), calls Agent Core
for the response, synthesises audio via Raya TTS, and sends audio back to Vobiz.
One instance is shared across all concurrent calls; per-call state is in _active_calls.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from src.agent_core_service import AgentCoreLLMService
from src.base import TelephonyAdapterBase, TelephonyError
from src.raya_stt_service import RayaSTTService
from src.raya_tts_service import RayaTTSService
from src.vobiz_serializer import VobizFrameSerializer

logger = logging.getLogger(__name__)


class VobizTelephonyAdapter(TelephonyAdapterBase):
    """Handles the full lifecycle of inbound and outbound Vobiz calls.

    One instance serves all concurrent calls. Per-call state (session_id, stream_sid)
    lives in _active_calls keyed by call_sid. Service instances are created once at
    construction and shared across calls.

    Args:
        config: Full merged config dict. Reads telephony_adapter section.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        self._config = config
        self._serializer = VobizFrameSerializer()
        self._stt = RayaSTTService(config)
        self._tts = RayaTTSService(config)
        self._ac = AgentCoreLLMService(config)
        self._active_calls: dict[str, dict] = {}
        from opentelemetry import metrics as _otel_metrics
        _meter = _otel_metrics.get_meter("telephony_adapter")
        self._active_calls_gauge = _meter.create_up_down_counter(
            "telephony.active_calls",
            description="Number of concurrent active calls",
        )
        self._turn_latency_hist = _meter.create_histogram(
            "telephony.turn.latency_ms",
            description="End-to-end per-turn latency in milliseconds",
        )

    async def handle_call(self, call_sid: str, caller_id: str, websocket) -> None:
        """Handle the full lifecycle of a Vobiz call over a WebSocket connection.

        Reads WebSocket messages in order:
        - "start": extracts stream_sid for outbound audio framing
        - "media": accumulates audio bytes
        - "stop": transcribes accumulated audio, calls Agent Core, synthesises TTS,
                  sends audio back, closes WebSocket if escalated

        Empty transcripts (silence) are discarded without calling Agent Core.

        Args:
            call_sid: Unique Vobiz call identifier.
            caller_id: Caller phone number (opaque — not forwarded to Agent Core).
            websocket: Active WebSocket connection from FastAPI.

        Raises:
            TelephonyError: If the WebSocket cannot be read.
        """
        from opentelemetry import trace as _otel_trace
        tracer = _otel_trace.get_tracer("telephony_adapter")

        session_id = str(uuid.uuid4())
        self._active_calls[call_sid] = {"session_id": session_id}
        audio_buffer: list[bytes] = []
        self._active_calls_gauge.add(1)

        logger.info(
            "telephony_adapter.call_start",
            extra={
                "operation": "telephony_adapter.handle_call",
                "status": "success",
                "call_sid": call_sid,
                "session_id": session_id,
            },
        )

        try:
            async for message in websocket:
                try:
                    event = json.loads(message).get("event", "")
                except json.JSONDecodeError as e:
                    logger.warning(
                        "telephony_adapter.message_parse_error",
                        extra={
                            "operation": "telephony_adapter.handle_call",
                            "status": "failure",
                            "call_sid": call_sid,
                            "error": f"JSONDecodeError: {e}",
                        },
                    )
                    continue

                if event == "media":
                    try:
                        chunk = self._serializer.parse_media(message)
                        if chunk:
                            audio_buffer.append(chunk)
                    except ValueError as e:
                        logger.warning(
                            "telephony_adapter.media_parse_error",
                            extra={
                                "operation": "telephony_adapter.handle_call",
                                "status": "failure",
                                "call_sid": call_sid,
                                "error": f"{type(e).__name__}: {e}",
                            },
                        )

                elif event == "stop":
                    if not audio_buffer:
                        break

                    audio = b"".join(audio_buffer)
                    audio_buffer = []

                    with tracer.start_as_current_span("telephony.turn") as span:
                        span.set_attribute("session_id", session_id)
                        span.set_attribute("call_sid", call_sid)
                        turn_start = time.time()

                        transcript = await self._stt.transcribe(audio)
                        if not transcript or not transcript.strip():
                            logger.info(
                                "telephony_adapter.empty_transcript",
                                extra={
                                    "operation": "telephony_adapter.handle_call",
                                    "status": "skipped",
                                    "call_sid": call_sid,
                                },
                            )
                            span.set_attribute("status", "skipped")
                            break

                        ac_result = await self._ac.process_turn(
                            session_id=session_id,
                            user_message=transcript,
                            call_sid=call_sid,
                            caller_id=caller_id,
                        )

                        audio_out = await self._tts.synthesize(ac_result.response_text)
                        if audio_out:
                            stream_sid = self._active_calls[call_sid].get("stream_sid", "")
                            out_msg = self._serializer.build_media_message(stream_sid, audio_out)
                            await websocket.send(out_msg)

                        turn_ms = int((time.time() - turn_start) * 1000)
                        self._turn_latency_hist.record(turn_ms)
                        span.set_attribute("latency_ms", turn_ms)
                        span.set_attribute("was_escalated", ac_result.was_escalated)
                        span.set_attribute("status", "success")

                    if ac_result.was_escalated:
                        logger.info(
                            "telephony_adapter.escalated",
                            extra={
                                "operation": "telephony_adapter.handle_call",
                                "status": "success",
                                "call_sid": call_sid,
                            },
                        )
                        await websocket.close()
                        break

                elif event == "start":
                    try:
                        metadata = self._serializer.parse_start(message)
                        self._active_calls[call_sid]["stream_sid"] = metadata.stream_sid
                    except ValueError as e:
                        logger.warning(
                            "telephony_adapter.start_parse_error",
                            extra={
                                "operation": "telephony_adapter.handle_call",
                                "status": "failure",
                                "call_sid": call_sid,
                                "error": f"{type(e).__name__}: {e}",
                            },
                        )

        except Exception as e:
            logger.error(
                "telephony_adapter.error",
                extra={
                    "operation": "telephony_adapter.handle_call",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
        finally:
            self._active_calls_gauge.add(-1)
            await self.teardown(call_sid)

    async def teardown(self, call_sid: str) -> None:
        """Release resources for a completed or dropped call.

        Args:
            call_sid: The call SID whose state should be removed.
        """
        self._active_calls.pop(call_sid, None)
        logger.info(
            "telephony_adapter.teardown",
            extra={
                "operation": "telephony_adapter.teardown",
                "status": "success",
                "call_sid": call_sid,
            },
        )
