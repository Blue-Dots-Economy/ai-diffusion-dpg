"""
reach_layer/voice/src/vobiz_adapter.py

VobizAdapter — concrete TelephonyAdapterBase for the Vobiz telephony platform.

Owns the full per-call lifecycle: parse handshake, build Pipecat pipeline,
run until call ends, teardown. Composes VobizOperator, SileroVADWrapper,
RayaSTTService, AgentCoreLLMProcessor, and RayaTTSService.

caller_id (E.164 phone number) is used as user_id so the Memory Layer can
recognise returning callers across sessions.
Belongs to the Reach Layer / Voice channel in the DPG framework.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import WebSocket
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from reach_layer_base import VADEvent

from src.base import TelephonyAdapterBase, TelephonyError
from src.operators.vobiz_operator import VobizOperator
from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
from src.pipecat_services.raya_stt import RayaSTTService
from src.pipecat_services.raya_tts import RayaTTSService
from src.pipecat_services.tts_sanitizer import TTSTextSanitizerProcessor
from src.vad.silero_vad import SileroVADWrapper

logger = logging.getLogger(__name__)


class VobizAdapter(TelephonyAdapterBase):
    """Telephony adapter for the Vobiz platform.

    Implements TelephonyAdapterBase by composing operator, VAD, STT, LLM,
    and TTS components into a Pipecat pipeline. One instance is created per
    call and discarded after teardown.

    Args:
        config: Full merged config dict.

    Raises:
        ValueError: If config is None.
    """

    def __init__(self, config: dict) -> None:
        # Initialise ReachLayerBase → inherits submit_input / subscribe_events /
        # cancel_turn HTTP helpers and channel_name/assembly_mode accessors.
        # When assembly_mode is "session" the embedded AgentCoreLLMProcessor
        # uses these helpers to stream SentenceEvents back to TTS as they
        # arrive; in "direct" mode the processor falls back to its own
        # synchronous POST to /process_turn.
        super().__init__(config, channel_name="voice")
        self._operator = VobizOperator(config)
        self._vad_wrapper = SileroVADWrapper()
        ac_cfg = config.get("telephony_adapter", {}).get("agent_core", {})
        self._greeting = ac_cfg.get("greeting", "Hello, how can I help you today?")
        self._sample_rate = int(
            config.get("telephony_adapter", {}).get("vobiz", {}).get("sample_rate", 8000)
        )
        # GH-137: reference to the active call's WebSocket, populated inside
        # handle_call(). Used by close_call() to terminate the call when Agent
        # Core signals a session end (DoneEvent.session_ended=True).
        self._active_websocket: WebSocket | None = None

    async def handle_call(self, call_sid: str, caller_id: str, websocket: WebSocket) -> None:
        """Handle the full lifecycle of one Vobiz inbound call.

        Parses the Vobiz handshake, builds the VAD→STT→Agent→TTS pipeline,
        sends a greeting on connect, and runs until the call ends.

        Args:
            call_sid: Vobiz CallUUID — opaque call identifier.
            caller_id: Caller E.164 phone number, used as user_id for Memory Layer.
            websocket: Accepted WebSocket connection from Vobiz.

        Raises:
            TelephonyError: If the handshake fails.
        """
        logger.info(
            "vobiz_adapter.handle_call_start",
            extra={
                "operation": "vobiz_adapter.handle_call",
                "status": "success",
                "call_sid": call_sid,
                "caller_id": caller_id,
            },
        )
        try:
            stream_id, call_id = await self._operator.parse_handshake(websocket)
        except Exception as exc:
            logger.error(
                "vobiz_adapter.handshake_failed",
                extra={
                    "operation": "vobiz_adapter.handle_call",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise TelephonyError(f"Handshake failed for {call_sid}: {exc}") from exc

        logger.info(
            "vobiz_adapter.handshake_ok",
            extra={
                "operation": "vobiz_adapter.handle_call",
                "status": "success",
                "call_sid": call_sid,
                "stream_id": stream_id,
                "call_id": call_id,
            },
        )
        transport = self._operator.create_transport(
            websocket, stream_id, call_id or call_sid
        )
        vad_analyzer = self._vad_wrapper.create_analyzer(self._config)
        session_id = str(uuid.uuid4())

        stt = RayaSTTService(self._config)
        # GH-137: pass the top-level channels.voice block and the adapter itself
        # so the processor can append the terminal word and request a call close
        # when Agent Core signals DoneEvent.session_ended=True.
        channel_config = (
            self._config.get("channels", {}).get("voice", {})
            if isinstance(self._config, dict)
            else {}
        )
        self._active_websocket = websocket
        agent = AgentCoreLLMProcessor(
            self._config,
            call_sid=call_sid,
            session_id=session_id,
            user_id=caller_id,
            channel=self,
            channel_config=channel_config,
            telephony=self,
        )
        tts = RayaTTSService(self._config)
        sanitizer = TTSTextSanitizerProcessor()

        pipeline = Pipeline(
            [
                transport.input(),
                VADProcessor(vad_analyzer=vad_analyzer),
                stt,
                agent,
                sanitizer,
                tts,
                transport.output(),
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=self._sample_rate,
                audio_out_sample_rate=self._sample_rate,
            ),
        )

        @transport.event_handler("on_client_connected")
        async def _on_connected(transport, client):
            logger.info(
                "vobiz_adapter.call_connected",
                extra={
                    "operation": "vobiz_adapter.handle_call",
                    "status": "success",
                    "call_sid": call_sid,
                    "session_id": session_id,
                },
            )
            try:
                await task.queue_frame(TTSSpeakFrame(text=self._greeting))
            except Exception as exc:
                logger.error(
                    "vobiz_adapter.greeting_failed",
                    extra={
                        "operation": "vobiz_adapter._on_connected",
                        "status": "failure",
                        "error": f"{type(exc).__name__}: {exc}",
                        "call_sid": call_sid,
                    },
                )

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnected(transport, client):
            logger.info(
                "vobiz_adapter.call_disconnected",
                extra={
                    "operation": "vobiz_adapter.handle_call",
                    "status": "success",
                    "call_sid": call_sid,
                },
            )
            try:
                await task.cancel()
            except Exception as exc:
                logger.warning(
                    "vobiz_adapter.cancel_failed",
                    extra={
                        "operation": "vobiz_adapter._on_disconnected",
                        "status": "failure",
                        "error": f"{type(exc).__name__}: {exc}",
                        "call_sid": call_sid,
                    },
                )

        runner = PipelineRunner(handle_sigint=False)
        await runner.run(task)

    async def teardown(self, call_sid: str) -> None:
        """Log call completion. Pipecat handles WebSocket resource cleanup.

        Args:
            call_sid: The call SID whose resources should be released.
        """
        logger.info(
            "vobiz_adapter.teardown",
            extra={
                "operation": "vobiz_adapter.teardown",
                "status": "success",
                "call_sid": call_sid,
            },
        )

    # ------------------------------------------------------------------
    # ReachLayerBase / VoiceChannelBase lifecycle hooks
    #
    # Design decision (not in spec): These hooks are implemented as
    # structured-log no-ops for now. The current VobizAdapter delegates the
    # full per-call lifecycle to handle_call() / teardown() + the Pipecat
    # pipeline, so there is no separate session_start / session_end or
    # barge-in / VAD dispatch path yet. The hooks are defined so the class
    # satisfies the unified Reach Layer base contract (issue #73) and so
    # future streaming refactors (issue #71 follow-ups) have observable
    # extension points without another signature change.
    # ------------------------------------------------------------------

    async def close_call(self, *, reason: str = "normal") -> None:
        """Close the active call (GH-137).

        Invoked by AgentCoreLLMProcessor when Agent Core signals
        DoneEvent.session_ended=True so the telephony leg is released after the
        terminal word is spoken.

        Args:
            reason: Free-form reason string recorded in structured logs.
        """
        logger.info(
            "vobiz_adapter.close_call",
            extra={
                "operation": "vobiz_adapter.close_call",
                "status": "invoked",
                "reason": reason,
            },
        )
        ws = self._active_websocket
        if ws is None:
            logger.warning(
                "vobiz_adapter.close_call_no_active_ws",
                extra={
                    "operation": "vobiz_adapter.close_call",
                    "status": "skipped",
                    "reason": "no active websocket",
                },
            )
            return
        try:
            await ws.close()
        except Exception as exc:
            logger.error(
                "vobiz_adapter.close_call_failed",
                extra={
                    "operation": "vobiz_adapter.close_call",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    async def on_session_start(self, session_id: str, user_id: str) -> None:
        """No-op. Voice sessions are established inside handle_call()."""
        logger.info(
            "vobiz_adapter.session_start",
            extra={
                "operation": "vobiz_adapter.on_session_start",
                "status": "skipped",
                "reason": "session lifecycle owned by handle_call/teardown",
                "session_id": session_id,
                "user_id": user_id or "anonymous",
            },
        )

    async def on_session_end(self, session_id: str) -> None:
        """No-op. Voice sessions tear down inside teardown()."""
        logger.info(
            "vobiz_adapter.session_end",
            extra={
                "operation": "vobiz_adapter.on_session_end",
                "status": "skipped",
                "reason": "session lifecycle owned by handle_call/teardown",
                "session_id": session_id,
            },
        )

    async def handle_barge_in(self, session_id: str) -> None:
        """No-op. Barge-in is handled automatically by TurnAssembler.

        When new input arrives via submit_input() while a turn is in flight,
        TurnAssembler.add_segment() detects the INVOKED state and calls cancel()
        internally — no explicit cancel from the Reach Layer is needed.

        Args:
            session_id: The session whose active turn should be cancelled.
        """
        logger.info(
            "vobiz_adapter.barge_in",
            extra={
                "operation": "vobiz_adapter.handle_barge_in",
                "status": "skipped",
                "reason": "barge-in handled by TurnAssembler on next add_segment()",
                "session_id": session_id,
            },
        )

    async def on_vad_event(self, session_id: str, event: VADEvent) -> None:
        """Placeholder VAD event hook. Observability-only today; the Pipecat
        VADProcessor drives the audio pipeline directly.
        """
        logger.info(
            "vobiz_adapter.vad_event",
            extra={
                "operation": "vobiz_adapter.on_vad_event",
                "status": "success",
                "session_id": session_id,
                "event_type": event.event_type,
                "timestamp_ms": event.timestamp_ms,
                "duration_ms": event.duration_ms,
            },
        )
