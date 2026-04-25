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

import asyncio
import logging
import time
import uuid

from fastapi import WebSocket
from pipecat.frames.frames import TTSSpeakFrame
from reach_layer_base import DoneEvent, SentenceEvent
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.turns.user_start.vad_user_turn_start_strategy import (
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies
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

        # GH-152: UserTurnProcessor sits between VAD and STT so a
        # VADUserStartedSpeakingFrame during bot TTS emits an InterruptionFrame
        # which flushes the TTS queue. VAD-based stop strategy avoids the
        # default LocalSmartTurn ML model download.
        vad_cfg = self._config.get("telephony_adapter", {}).get("vad", {})
        user_speech_timeout = float(vad_cfg.get("stop_secs", 0.6))
        user_turn_processor = UserTurnProcessor(
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[SpeechTimeoutUserTurnStopStrategy(
                    user_speech_timeout=user_speech_timeout
                )],
            ),
        )

        pipeline = Pipeline(
            [
                transport.input(),
                VADProcessor(vad_analyzer=vad_analyzer),
                user_turn_processor,
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
            # GH-149: eagerly open an SSE subscription so Agent Core can push
            # the entry subagent's opening_phrase before the caller speaks.
            # Runs as a background task so it doesn't block on_client_connected
            # (Pipecat requires the handler to return promptly). The task
            # consumes SentenceEvents as TTSSpeakFrames and exits on DoneEvent,
            # closing the HTTP stream so the per-turn subscribe in
            # AgentCoreLLMProcessor owns the session queue from then on.
            asyncio.create_task(
                self._play_opening_phrase(task, session_id, caller_id, call_sid)
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

    async def _play_opening_phrase(
        self,
        pipeline_task: PipelineTask,
        session_id: str,
        caller_id: str,
        call_sid: str,
    ) -> None:
        """Consume the first SSE turn and speak any opening_phrase sentences.

        Opens a ``?user_id=<caller_id>`` SSE subscription to Agent Core. If the
        session is new, Agent Core emits the entry subagent's opening_phrase as
        a SentenceEvent + DoneEvent pair; each sentence is queued as a
        TTSSpeakFrame so the caller hears it immediately. Exits on DoneEvent,
        closing the HTTP stream so the per-turn subscription in
        AgentCoreLLMProcessor owns the session queue afterwards. On reconnect
        (flag already set), Agent Core emits nothing and this task exits
        silently. Errors never propagate — the normal transcription path still
        works even if this task fails.

        Args:
            pipeline_task: The active PipelineTask; frames are queued onto it.
            session_id: Session identifier for the SSE URL.
            caller_id: Caller E.164 number used as user_id in the SSE query param.
            call_sid: Vobiz call identifier, for log correlation.
        """
        try:
            async for event in self.subscribe_events(
                session_id, user_id=caller_id or None
            ):
                if isinstance(event, SentenceEvent) and event.text:
                    await pipeline_task.queue_frame(TTSSpeakFrame(text=event.text))
                elif isinstance(event, DoneEvent):
                    break
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "vobiz_adapter.opening_phrase_failed",
                extra={
                    "operation": "vobiz_adapter._play_opening_phrase",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "call_sid": call_sid,
                    "session_id": session_id,
                },
            )

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
        """Defensive fallback close for the active Vobiz call (GH-137, GH-199).

        On the happy path the bot-initiated end-of-session is driven by an
        ``EndFrame`` pushed through the pipecat pipeline from
        ``AgentCoreLLMProcessor._handle_done_event``; the
        ``LoggingVobizFrameSerializer`` then issues the Vobiz REST DELETE
        and the underlying WebSocket is closed by pipecat's pipeline
        shutdown. This method remains as a defensive fallback for paths
        that bypass that flow (e.g. errors raised before the EndFrame is
        pushed, or non-pipeline-mediated session terminations).
        ``LoggingVobizFrameSerializer`` is idempotent thanks to its
        ``_hangup_attempted`` guard, so calling this method after a clean
        EndFrame shutdown is safe.

        Args:
            reason: Free-form reason string recorded in structured logs.
        """
        start = time.time()
        logger.info(
            "vobiz_adapter.close_call",
            extra={
                "operation": "vobiz_adapter.close_call",
                "status": "invoked",
                "reason": reason,
                "vendor_signal": "vobiz_rest_delete",
            },
        )
        ws = self._active_websocket
        if ws is None:
            logger.warning(
                "vobiz_adapter.close_call_no_active_ws",
                extra={
                    "operation": "vobiz_adapter.close_call",
                    "status": "skipped",
                    "outcome": "skipped",
                    "reason": "no active websocket",
                    "vendor_signal": "vobiz_rest_delete",
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
                    "outcome": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "vendor_signal": "vobiz_rest_delete",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return
        logger.info(
            "vobiz_adapter.close_call_complete",
            extra={
                "operation": "vobiz_adapter.close_call",
                "status": "success",
                "outcome": "ws_closed",
                "vendor_signal": "vobiz_rest_delete",
                "latency_ms": int((time.time() - start) * 1000),
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
        """No-op. Barge-in is handled inside the Pipecat pipeline (GH-152).

        End-to-end barge-in involves two independent layers that this
        adapter wires up but does not drive explicitly:

        1. **Voice pipeline side (immediate, audio-level).** The
           UserTurnProcessor installed between VADProcessor and STT in
           handle_call() converts VADUserStartedSpeakingFrame into a
           pipecat InterruptionFrame when the bot is currently speaking.
           Pipecat flushes the TTS queue, and AgentCoreLLMProcessor's
           _start_interruption() override stops forwarding SentenceEvents
           and optionally speaks the configured
           ``agent_core.barge_in_acknowledgement`` template.

        2. **Agent Core side (deferred, turn-logic level).** When the
           caller's new utterance is transcribed and submitted as a
           segment, TurnAssembler.add_segment() sees the active turn is
           INVOKED, calls cancel() (emitting DoneEvent(interrupted)),
           and — per GH-152 Phase 2 — discards the original segments so
           only the barge-in speech drives the next turn.

        Neither path routes through this method. It exists to satisfy the
        VoiceChannelBase contract and to provide a single observability
        breadcrumb if a future caller routes explicit barge-in signals here.

        Args:
            session_id: The session whose active turn should be cancelled.
        """
        logger.info(
            "vobiz_adapter.barge_in",
            extra={
                "operation": "vobiz_adapter.handle_barge_in",
                "status": "skipped",
                "reason": "barge-in is handled by UserTurnProcessor + TurnAssembler (GH-152)",
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
