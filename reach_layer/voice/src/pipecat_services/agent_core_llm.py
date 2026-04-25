"""
telephony_adapter/src/pipecat_services/agent_core_llm.py

AgentCoreLLMProcessor — Pipecat FrameProcessor that bridges TranscriptionFrames
to Agent Core.

Receives TranscriptionFrame from RayaSTTService, forwards each utterance to
Agent Core, then pushes TTSSpeakFrame(s) downstream so RayaTTSService can
synthesize the response.

Routing is driven by ``reach_layer.channels.voice.assembly_mode``:

* ``direct``  — POSTs to /process_turn (synchronous) and pushes a single
                TTSSpeakFrame containing the full response text.
* ``session`` — POSTs to /sessions/{id}/input via the channel's submit_input()
                helper, then consumes SSE events via subscribe_events().
                Each SentenceEvent is pushed as a separate TTSSpeakFrame so
                TTS playback can begin while Agent Core is still generating.

On was_escalated=True (in either mode), an EndFrame is pushed after the speak
frame(s) to close the pipeline gracefully (VobizFrameSerializer hangs up the
call on EndFrame). On HTTP error or timeout, a TTSSpeakFrame with the
configured fallback phrase is pushed so the call continues rather than
hanging silently.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    TextFrame,
    TTSSpeakFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from reach_layer_base import DoneEvent, ReachLayerBase, SentenceEvent, SignalEvent

logger = logging.getLogger(__name__)


class AgentCoreLLMProcessor(FrameProcessor):
    """Posts each transcribed utterance to Agent Core and pushes TTS response downstream.

    Args:
        config: Full merged config dict. Reads telephony_adapter.agent_core for
            direct-mode HTTP target, and reach_layer.channels.voice.assembly_mode
            to choose between direct and session routing.
        call_sid: Opaque Vobiz call identifier.
        session_id: Stable session UUID for this call's lifetime.
        user_id: Caller E.164 phone number — stable cross-call identifier passed to
            Agent Core so the Memory Layer can recognise returning callers.
        channel: ReachLayerBase instance providing submit_input/subscribe_events
            HTTP helpers. Required when assembly_mode is "session"; optional in
            direct mode (preserved as None for backwards compatibility with
            existing tests that construct the processor in isolation).

    Raises:
        ValueError: If agent_core.base_url is missing or empty, or if
            assembly_mode is "session" but no channel was provided.
    """

    def __init__(
        self,
        config: dict,
        *,
        call_sid: str,
        session_id: str,
        user_id: str = "",
        channel: Optional[ReachLayerBase] = None,
        channel_config: Optional[dict] = None,
        telephony: Optional[object] = None,
    ) -> None:
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
        # GH-152: optional template spoken when the caller interrupts the bot.
        # Empty string → go silent on barge-in (pipecat still flushes TTS).
        self._barge_in_acknowledgement: str = ac_cfg.get("barge_in_acknowledgement", "") or ""
        self._interrupted: bool = False
        # Tracks whether the bot is currently playing TTS. Set when the output
        # transport emits BotStartedSpeakingFrame; cleared on
        # BotStoppedSpeakingFrame. Used to gate the barge-in acknowledgement
        # so it only plays when the caller actually cut the bot off, not on
        # every user-turn-start (pipecat's UserTurnProcessor emits
        # InterruptionFrame on every VAD-start regardless of bot state).
        self._bot_speaking: bool = False
        self._call_sid = call_sid
        self._session_id = session_id
        self._user_id = user_id
        self._channel = channel
        # Per-channel runtime config (GH-137). Falls back to the top-level
        # ``channels.voice`` block in the merged config so callers that only
        # pass ``config`` keep working without plumbing changes.
        if channel_config is None:
            channel_config = (
                config.get("channels", {}).get("voice", {}) if isinstance(config, dict) else {}
            )
        self._channel_config = channel_config or {}
        self._telephony = telephony

        # Read assembly_mode from reach_layer.channels.voice. Defaults to "direct"
        # so existing tests and pre-config installs keep their current behaviour.
        self._assembly_mode = (
            config.get("reach_layer", {})
            .get("channels", {})
            .get("voice", {})
            .get("assembly_mode", "direct")
        )
        if self._assembly_mode == "session" and self._channel is None:
            raise ValueError(
                "assembly_mode='session' requires a channel reference "
                "(VobizAdapter must pass channel=self to AgentCoreLLMProcessor)"
            )

        logger.info(
            "agent_core_llm.init",
            extra={
                "operation": "agent_core_llm.init",
                "status": "success",
                "assembly_mode": self._assembly_mode,
                "call_sid": call_sid,
                "session_id": session_id,
            },
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Route frames to Agent Core; pass all other frames through.

        TranscriptionFrame → forward utterance to Agent Core (direct or session).
        Barge-in is handled via _start_interruption() below (GH-152) plus
        TurnAssembler's own cancel() on the Agent Core side.

        Args:
            frame: Incoming pipeline frame.
            direction: Direction of frame flow in the pipeline.
        """
        await super().process_frame(frame, direction)

        # Track bot-speaking state (set by the output transport after it
        # actually starts pushing audio to the caller).
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False

        if isinstance(frame, TranscriptionFrame):
            await self._handle_transcription(frame)
        else:
            await self.push_frame(frame, direction)

    async def _start_interruption(self) -> None:
        """Handle an InterruptionFrame (barge-in) from the UserTurnProcessor.

        Sets the in-flight ``_interrupted`` flag so the active SSE consumer
        loop exits on its next iteration (closing the HTTP stream, which in
        turn ends the Agent Core-side ``stream_turn``). When the bot was
        actually playing TTS at the moment the interruption fired, also
        pushes the configured acknowledgement phrase so the caller hears
        that the interruption landed. Pipecat's own machinery handles
        flushing the already-queued TTS audio — see
        FrameProcessor._start_interruption.

        The acknowledgement is gated on ``_bot_speaking`` because pipecat's
        UserTurnProcessor fires InterruptionFrame on every user-turn-start
        (not only when the bot is mid-response); without the gate the
        phrase would play before every user turn, not just on real
        interruptions.

        Called by the pipecat framework, not by user code.
        """
        await super()._start_interruption()
        was_speaking = self._bot_speaking
        self._interrupted = True
        logger.info(
            "agent_core_llm.interruption",
            extra={
                "operation": "agent_core_llm._start_interruption",
                "status": "success",
                "call_sid": self._call_sid,
                "session_id": self._session_id,
                "bot_was_speaking": was_speaking,
                "has_acknowledgement": bool(self._barge_in_acknowledgement),
            },
        )
        if was_speaking and self._barge_in_acknowledgement:
            await self.push_frame(
                TTSSpeakFrame(text=self._barge_in_acknowledgement)
            )

    async def _handle_transcription(self, frame: TranscriptionFrame) -> None:
        """Call Agent Core and push TTSSpeakFrame (and EndFrame on escalation).

        Routes to the session or direct path based on assembly_mode.

        Args:
            frame: The transcription frame containing the caller's utterance.
        """
        if self._assembly_mode == "session":
            await self._handle_transcription_session(frame)
        else:
            await self._handle_transcription_direct(frame)

    async def _handle_transcription_direct(self, frame: TranscriptionFrame) -> None:
        """Direct mode: synchronous POST /process_turn → single TTSSpeakFrame."""
        start = time.time()
        url = f"{self._base_url}/process_turn"
        # Channel must match a key under agent_core.channels.* so the
        # orchestrator can resolve the channel-specific system_prompt_suffix
        # and tts_rules. Pull from the ReachLayer adapter (VobizAdapter sets
        # channel_name="voice") rather than hardcoding a string that drifts
        # from config.
        channel_name = (getattr(self._channel, "channel_name", None)
            if self._channel is not None else None) or "voice"
        payload = {
            "session_id": self._session_id,
            "user_message": frame.text,
            "channel": channel_name,
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

    async def _handle_transcription_session(self, frame: TranscriptionFrame) -> None:
        """Session mode: submit_input + SSE stream → one TTSSpeakFrame per sentence.

        This is the low-latency path: as Agent Core emits each SentenceEvent,
        we immediately push it as a TTSSpeakFrame so RayaTTSService can begin
        synthesising the next sentence while the LLM is still generating later
        sentences. On DoneEvent we close out (and push EndFrame on escalation).
        On any error, we fall back to a single TTSSpeakFrame with the configured
        fallback phrase so the call doesn't hang silently.

        Args:
            frame: The transcription frame containing the caller's utterance.
        """
        start = time.time()
        sentences_pushed = 0
        was_escalated = False
        was_interrupted = False
        # GH-152: a fresh user turn means any prior barge-in is resolved.
        self._interrupted = False

        try:
            await self._channel.submit_input(
                self._session_id, frame.text, self._user_id or None
            )
        except Exception as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "agent_core_llm.submit_input_error",
                extra={
                    "operation": "agent_core_llm.submit_input",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": latency_ms,
                    "call_sid": self._call_sid,
                },
            )
            await self.push_frame(TTSSpeakFrame(text=self._fallback_phrase))
            return

        try:
            async for event in self._channel.subscribe_events(
                self._session_id, user_id=self._user_id or None
            ):
                # GH-152: exit early on barge-in so already-queued and
                # still-arriving SentenceEvents don't reach TTS. The pipecat
                # framework has already flushed in-flight TTS audio via the
                # system-level InterruptionFrame before this flag is set.
                if self._interrupted:
                    was_interrupted = True
                    logger.info(
                        "agent_core_llm.interrupted_during_stream",
                        extra={
                            "operation": "agent_core_llm.subscribe_events",
                            "status": "skipped",
                            "sentences_pushed": sentences_pushed,
                            "call_sid": self._call_sid,
                        },
                    )
                    break
                if isinstance(event, SentenceEvent):
                    if event.text:
                        await self.push_frame(TTSSpeakFrame(text=event.text))
                        sentences_pushed += 1
                elif isinstance(event, SignalEvent):
                    logger.debug(
                        "agent_core_llm.signal",
                        extra={
                            "operation": "agent_core_llm.subscribe_events",
                            "status": "success",
                            "stage": event.stage,
                            "signal_status": event.status,
                            "call_sid": self._call_sid,
                        },
                    )
                elif isinstance(event, DoneEvent):
                    was_escalated = event.was_escalated
                    was_interrupted = event.turn_status in ("interrupted", "abandoned")
                    logger.info(
                        "agent_core_llm.done",
                        extra={
                            "operation": "agent_core_llm.subscribe_events",
                            "status": "success",
                            "latency_ms": int((time.time() - start) * 1000),
                            "sentences_pushed": sentences_pushed,
                            "was_escalated": was_escalated,
                            "was_tool_used": event.was_tool_used,
                            "turn_status": event.turn_status,
                            "session_ended": getattr(event, "session_ended", False),
                            "call_sid": self._call_sid,
                        },
                    )
                    await self._handle_done_event(event)
                    break
        except Exception as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.error(
                "agent_core_llm.subscribe_events_error",
                extra={
                    "operation": "agent_core_llm.subscribe_events",
                    "status": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": latency_ms,
                    "sentences_pushed": sentences_pushed,
                    "call_sid": self._call_sid,
                },
            )
            if sentences_pushed == 0:
                await self.push_frame(TTSSpeakFrame(text=self._fallback_phrase))
            return

        # Nothing came through (no sentences, no done) → speak fallback so the
        # caller doesn't sit in silence. Skip on barge-in (interrupted/abandoned)
        # because the caller already started speaking — don't talk over them.
        if sentences_pushed == 0 and not was_interrupted:
            await self.push_frame(TTSSpeakFrame(text=self._fallback_phrase))

        if was_escalated:
            logger.info(
                "agent_core_llm.escalated",
                extra={
                    "operation": "agent_core_llm.subscribe_events",
                    "status": "success",
                    "call_sid": self._call_sid,
                },
            )
            await self.push_frame(EndFrame())

    async def _handle_done_event(self, event: DoneEvent) -> None:
        """Handle session-ending semantics on a DoneEvent (GH-137, GH-199).

        When ``event.session_ended`` is True, push the configured terminal word
        as a final utterance frame (so TTS speaks it before the call drops) and
        push an ``EndFrame`` so the pipeline drains and the Vobiz serializer
        issues its REST DELETE hangup. We do NOT call ``telephony.close_call``
        here — closing the WebSocket eagerly races the EndFrame and prevents
        it from reaching the serializer, leaving Vobiz holding the call leg
        alive (observed under #199). ``close_call`` remains as a defensive
        fallback for paths that bypass this flow.

        Args:
            event: The DoneEvent emitted by Agent Core at end of turn.
        """
        if not getattr(event, "session_ended", False):
            return

        terminal_word = (self._channel_config or {}).get("terminal_word", "") or ""
        if terminal_word:
            await self.push_frame(TextFrame(terminal_word))
        else:
            logger.warning(
                "agent_core_llm.session_ended_no_terminal_word",
                extra={
                    "operation": "agent_core_llm.done",
                    "status": "skipped",
                    "reason": "terminal_word empty",
                    "call_sid": self._call_sid,
                },
            )

        # GH-199: push EndFrame so the Vobiz serializer can issue its REST
        # DELETE hangup. This is the load-bearing step — ws.close() alone is
        # not enough to drop the telephony leg, and calling close_call() here
        # races the EndFrame and prevents the REST hangup from firing.
        await self.push_frame(EndFrame())

        logger.info(
            "agent_core_llm.session_ended",
            extra={
                "operation": "agent_core_llm.done",
                "status": "success",
                "call_sid": self._call_sid,
            },
        )
