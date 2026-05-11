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
from opentelemetry import trace as otel_trace
from pipecat.frames.frames import TTSSpeakFrame
from reach_layer_base import ConsentEvent, DoneEvent, SentenceEvent
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
from src.recordings.factory import build_recording_manager
from src.recordings.manager_base import RecordingManagerBase
from src.recordings.telemetry import SignalEmitter, recording_lifecycle_span
from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
from src.pipecat_services.raya_stt import RayaSTTService
from src.pipecat_services.raya_tts import RayaTTSService
from src.pipecat_services.tts_sanitizer import TTSTextSanitizerProcessor
from src.pipecat_services.vad_observer import VADObserverProcessor, run_voice_heartbeat
from src.vad.silero_vad import SileroVADWrapper

logger = logging.getLogger(__name__)


class _NoopObservability:
    """Fallback observability backend that logs signals without a real OBS instance."""

    def emit_signal(self, signal_type: str, data: dict) -> None:
        """Log the signal as a structured info record.

        Args:
            signal_type: The observability signal type string.
            data: Signal payload forwarded verbatim to the log record.
        """
        logger.info(
            "noop_observability.emit_signal",
            extra={
                "operation": "observability.emit_signal",
                "status": "skipped",
                "signal_type": signal_type,
                **data,
            },
        )


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
            config.get("reach_layer", {}).get("channels", {}).get("voice", {}).get("vobiz", {}).get("sample_rate", 8000)
        )
        # GH-137: reference to the active call's WebSocket, populated inside
        # handle_call(). Used by close_call() to terminate the call when Agent
        # Core signals a session end (DoneEvent.session_ended=True).
        self._active_websocket: WebSocket | None = None

        # Recording wiring — per-call state
        self._recording_url_registry: dict = {}
        rec_cfg = (
            self._config.get("reach_layer", {})
            .get("channels", {})
            .get("voice", {})
            .get("recording", {})
        )
        self._recording_consent_purpose: str = rec_cfg.get(
            "consent_purpose", "recording"
        )
        # Testing/disclosure-based deployments: start recording the moment the
        # websocket connects, bypassing the consent gate. Default False so
        # production stays consent-gated. See issue #332 for context.
        self._recording_start_on_connect: bool = bool(
            rec_cfg.get("start_on_connect", False)
        )
        # Placeholder manager rebuilt with call-specific identifiers inside
        # handle_call(); NullRecordingManager until the call is known.
        self._recording_manager: RecordingManagerBase = build_recording_manager(
            self._config,
            telephony=self,
            registry=self._recording_url_registry,
        )
        # OTel span context captured at handle_call() start for lifecycle link.
        self._inbound_span_context = None
        self._session_id_cache: str = ""

    @property
    def recording_manager(self) -> RecordingManagerBase:
        """The RecordingManagerBase instance for the current call.

        Returns NullRecordingManager when recording is disabled; never None.
        """
        return self._recording_manager

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
        self._session_id_cache = session_id

        # Rebuild the recording manager keyed to this specific call so
        # call_sid / session_id / caller_id are baked in from the start.
        public_url = (
            self._config.get("reach_layer", {})
            .get("channels", {})
            .get("voice", {})
            .get("public_url", "")
        )
        callback_url = (
            f"{public_url.rstrip('/')}/recording-ready" if public_url else ""
        )
        self._recording_manager = build_recording_manager(
            self._config,
            telephony=self,
            registry=self._recording_url_registry,
            call_sid=call_sid,
            session_id=session_id,
            caller_id=caller_id,
            vobiz_call_id=call_id or "",
            callback_url=callback_url,
        )
        current_span = otel_trace.get_current_span()
        if current_span is not None:
            self._inbound_span_context = current_span.get_span_context()

        stt = RayaSTTService(self._config)
        # GH-137 / GH-242: pass the voice channel config (terminal_word,
        # filler_phrase, filler_threshold_ms, …) so the processor can act on
        # them. The reach_layer config loader stores the canonical voice
        # block under ``reach_layer.channels.voice``; the legacy
        # ``channels.voice`` top-level path was never populated by
        # ``_inject_legacy_aliases``, so reading from it returned ``{}``
        # and the filler timer / terminal_word were silently disabled.
        channel_config = (
            self._config.get("reach_layer", {})
            .get("channels", {})
            .get("voice", {})
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
        vad_cfg = self._config.get("reach_layer", {}).get("channels", {}).get("voice", {}).get("vad", {})
        user_speech_timeout = float(vad_cfg.get("stop_secs", 0.6))
        user_turn_processor = UserTurnProcessor(
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[SpeechTimeoutUserTurnStopStrategy(
                    user_speech_timeout=user_speech_timeout
                )],
            ),
        )

        # GH-238: passive observer between STT and the LLM processor — sees VAD
        # speech start/stop, user-turn start/stop, transcripts, and barge-in
        # frames. Pairs with a heartbeat task to surface pipeline stalls (case
        # in point: caller's follow-up never producing a 7th transcript after a
        # tool-using turn).
        vad_observer = VADObserverProcessor(
            session_id=session_id, call_sid=call_sid
        )

        pipeline = Pipeline(
            [
                transport.input(),
                VADProcessor(vad_analyzer=vad_analyzer),
                user_turn_processor,
                stt,
                vad_observer,
                agent,
                sanitizer,
                tts,
                *self._recording_manager.pipeline_processors,
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

        # GH-238: heartbeat interval (seconds) — disable by setting to 0 or a
        # negative number. Defaults to 10 s so a multi-second VAD/STT stall is
        # visible in logs without spamming on healthy calls.
        heartbeat_cfg = (
            self._config.get("reach_layer", {}).get("channels", {}).get("voice", {})
            .get("observability", {})
        )
        heartbeat_interval_s = float(heartbeat_cfg.get("heartbeat_interval_s", 10.0))
        heartbeat_task: asyncio.Task | None = None

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
            #
            # GH-202: register the task on the processor so it can cancel and
            # join it at the start of the first transcription turn — preventing
            # the opening-phrase subscribe and the per-turn subscribe from
            # racing on the session's shared event queue.
            opening_phrase_task = asyncio.create_task(
                self._play_opening_phrase(task, session_id, caller_id, call_sid)
            )
            agent.set_opening_phrase_task(opening_phrase_task)
            # Testing/disclosure path (#332): start the recorder immediately on
            # connect, bypassing the consent gate. Triggered by
            # reach_layer.channels.voice.recording.start_on_connect = true.
            if self._recording_start_on_connect:
                logger.info(
                    "vobiz_adapter.recording_start_on_connect",
                    extra={
                        "operation": "vobiz_adapter.handle_call",
                        "status": "invoked",
                        "call_sid": call_sid,
                        "session_id": session_id,
                        "reason": "start_on_connect=true",
                    },
                )
                asyncio.create_task(
                    self._recording_manager.start(consent_granted_ts=time.time())
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

        if heartbeat_interval_s > 0:
            heartbeat_task = asyncio.create_task(
                run_voice_heartbeat(
                    vad_observer,
                    session_id=session_id,
                    call_sid=call_sid,
                    interval_s=heartbeat_interval_s,
                )
            )

        runner = PipelineRunner(handle_sigint=False)
        try:
            await runner.run(task)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass

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
                elif isinstance(event, ConsentEvent):
                    await self._on_consent_event(event)
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
        """Log call completion and spawn recording finalize task.

        Pipecat handles WebSocket resource cleanup. Recording finalization
        runs as a background asyncio.Task so call teardown is never blocked.

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
        asyncio.create_task(self._finalize_and_store(call_sid))

    async def _on_consent_event(self, evt: ConsentEvent) -> None:
        """Start the recording manager when the user grants recording consent.

        No-op when ``evt.purpose`` does not match the configured
        ``recording.consent_purpose`` or when consent was not granted.

        Args:
            evt: The ConsentEvent received from the Agent Core SSE stream.
        """
        if evt.purpose != self._recording_consent_purpose or not evt.granted:
            return
        await self._recording_manager.start(
            consent_granted_ts=evt.consent_granted_ts or time.time()
        )

    async def _finalize_and_store(self, call_sid: str) -> None:
        """Finalize and persist the recording after call teardown.

        Wraps the full lifecycle in a ``recording.lifecycle`` OTel span linked
        to the inbound call span. Emits ``recording.stored``, ``recording.empty``,
        or ``recording.failed`` signals via SignalEmitter. All exceptions are
        caught and logged so call teardown is never disrupted.

        Args:
            call_sid: The telephony call identifier for log correlation.
        """
        # TODO(#330): replace _NoopObservability with the adapter's real
        # ObservabilityLayerBase client so recording.* signals land in audit.
        emitter = SignalEmitter(_NoopObservability())
        link = None
        if self._inbound_span_context is not None:
            link = otel_trace.Link(self._inbound_span_context)
        try:
            caller_id_hash = self._recording_manager.caller_id_hash
            source_name = self._recording_manager.source_name
            with recording_lifecycle_span(
                call_sid=call_sid,
                session_id=self._session_id_cache,
                caller_id_hash=caller_id_hash,
                source=source_name,
                link=link,
            ) as span:
                trace_id = format(span.get_span_context().trace_id, "032x")
                if hasattr(self._recording_manager, "attach_trace_id"):
                    self._recording_manager.attach_trace_id(trace_id)
                await self._recording_manager.stop()
                artifact = await self._recording_manager.finalize()
                if artifact is None:
                    if self._recording_manager.state == "failed":
                        emitter.failed(
                            call_sid=call_sid,
                            session_id=self._session_id_cache,
                            source=source_name,
                            stage="finalize",
                            error_type="",
                            error_message="",
                        )
                    else:
                        emitter.empty(
                            call_sid=call_sid,
                            session_id=self._session_id_cache,
                            source=source_name,
                            duration_ms=0,
                            reason="empty_or_idle",
                        )
                    return
                emitter.stored(
                    call_sid=call_sid,
                    session_id=artifact.session_id,
                    caller_id_hash=artifact.caller_id_hash,
                    source=artifact.source,
                    format=artifact.format,
                    duration_ms=artifact.duration_ms,
                    bytes=len(artifact.payload.bytes_data or b""),
                    sha256=artifact.sha256,
                    consent_granted_ts=artifact.consent_granted_ts,
                    start_ts=artifact.start_ts,
                    end_ts=artifact.end_ts,
                )
        except Exception as exc:
            logger.error(
                "vobiz_adapter.recording_pipeline_failed",
                extra={
                    "operation": "vobiz_adapter._finalize_and_store",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
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
