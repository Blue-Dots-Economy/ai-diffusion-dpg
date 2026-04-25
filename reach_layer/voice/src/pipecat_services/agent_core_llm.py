"""
reach_layer/voice/src/pipecat_services/agent_core_llm.py

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
import asyncio
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
        config: Full merged config dict. Reads reach_layer.channels.voice.agent_core for
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
        ac_cfg = config.get("reach_layer", {}).get("channels", {}).get("voice", {}).get("agent_core", {})
        base_url = ac_cfg.get("base_url", "").rstrip("/")
        if not base_url:
            raise ValueError(
                "reach_layer.channels.voice.agent_core.base_url is required. "
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
        # Retained for log compatibility only; the gate now uses the compound
        # signal below (GH-203). BotStartedSpeakingFrame / BotStoppedSpeakingFrame
        # still flip this for traceability in turn lifecycle logs.
        self._bot_speaking: bool = False
        self._call_sid = call_sid
        self._session_id = session_id
        self._user_id = user_id
        self._channel = channel
        # Per-channel runtime config (GH-137). Falls back to the canonical
        # ``reach_layer.channels.voice`` block in the merged config so
        # callers that only pass ``config`` keep working. GH-242: the prior
        # fallback read top-level ``channels.voice`` which is never
        # populated by the reach config loader, so terminal_word / filler
        # silently disabled themselves in production.
        if channel_config is None:
            channel_config = (
                config.get("reach_layer", {})
                .get("channels", {})
                .get("voice", {})
                if isinstance(config, dict)
                else {}
            )
        self._channel_config = channel_config or {}
        self._telephony = telephony
        # GH-202: Optional handle to the opening-phrase SSE consumer task
        # spawned by the adapter on client-connect. The processor cancels
        # it (and waits briefly for it to unwind) at the start of every
        # transcription turn so the per-turn SSE subscribe doesn't share
        # the session's event queue with a still-open opening-phrase
        # subscribe — preventing the frame-interleave race when a caller
        # speaks immediately after connect.
        self._opening_phrase_task: Optional[asyncio.Task] = None
        # Configurable upper bound on how long we wait for the opening-phrase
        # task to unwind after cancellation. Voice-startup latency budget is
        # 1.2s so this stays well under it.
        self._opening_phrase_join_timeout_s: float = float(
            self._channel_config.get("opening_phrase_join_timeout_ms", 500)
        ) / 1000.0

        # GH-205: optional filler utterance for slow turns. When the threshold
        # is set and the phrase is non-empty, the processor pushes a single
        # TTSSpeakFrame if no SentenceEvent has reached TTS within
        # ``filler_threshold_ms`` of submit_input completing. Capped to one
        # filler per turn — we never want to talk over the actual response.
        _filler_threshold = self._channel_config.get("filler_threshold_ms")
        self._filler_threshold_s: Optional[float] = (
            float(_filler_threshold) / 1000.0
            if _filler_threshold is not None and _filler_threshold > 0
            else None
        )
        self._filler_phrase: str = (
            self._channel_config.get("filler_phrase", "") or ""
        )

        # GH-203: compound gate for the barge-in acknowledgement. The original
        # ``_bot_speaking`` flag (driven by BotStartedSpeakingFrame /
        # BotStoppedSpeakingFrame) was unreliable on the Vobiz pipeline — the
        # stop-frame fired before audio finished draining, leaving the flag
        # stale so the ack played on every user-turn-start. We now gate on:
        #   leg "recency"    — last TTSSpeakFrame pushed within
        #                      ``barge_in_recency_ms`` (default 1500 ms);
        #   leg "sse_active" — an SSE turn is currently in flight (between
        #                      submit_input and DoneEvent / break).
        # Ack fires when at least one leg says the bot is actively speaking;
        # gate_leg is logged so this stays debuggable from production logs.
        self._last_tts_push_ts: float = 0.0
        self._sse_turn_active: bool = False
        self._barge_in_recency_ms: int = int(
            self._channel_config.get(
                "barge_in_recency_ms",
                ac_cfg.get("barge_in_recency_ms", 1500),
            )
        )

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

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        """Forward to the base implementation; record TTS push timestamps (GH-203).

        Records the monotonic time of every outbound TTSSpeakFrame so the
        barge-in gate's recency leg can detect whether the bot was actively
        speaking at the moment the InterruptionFrame arrived.
        """
        if isinstance(frame, TTSSpeakFrame):
            self._last_tts_push_ts = time.monotonic()
        return await super().push_frame(frame, direction)

    def _bot_actively_speaking(self) -> tuple[bool, str]:
        """Compound gate result for the barge-in acknowledgement (GH-203).

        Returns:
            (active, gate_leg) where ``active`` is True when at least one
            leg says the bot is actively speaking. ``gate_leg`` is one of
            "recency", "sse_active", "both", or "none" for log readability.
        """
        recency_open = False
        if self._last_tts_push_ts > 0.0 and self._barge_in_recency_ms > 0:
            elapsed_ms = (time.monotonic() - self._last_tts_push_ts) * 1000.0
            recency_open = elapsed_ms < self._barge_in_recency_ms
        sse_open = self._sse_turn_active
        if recency_open and sse_open:
            return True, "both"
        if recency_open:
            return True, "recency"
        if sse_open:
            return True, "sse_active"
        return False, "none"

    def set_opening_phrase_task(self, task: Optional[asyncio.Task]) -> None:
        """Register the opening-phrase SSE consumer task for serialisation (GH-202).

        The adapter calls this once after spawning ``_play_opening_phrase``
        from the on-client-connected handler. The processor cancels and
        joins the task before submitting the first user turn so the
        per-turn SSE subscribe doesn't race with the opening-phrase
        subscribe on the same session's shared event queue.

        Args:
            task: The opening-phrase consumer task, or None to clear.
        """
        self._opening_phrase_task = task

    async def _join_opening_phrase_task(self) -> None:
        """Cancel and await the opening-phrase task before opening a new SSE.

        No-op if no task is registered or it is already done. On normal
        completion (DoneEvent path) the task finishes naturally and cancel
        is a no-op. On the suppressed path (consent gate, P2-B) the
        server never emits DoneEvent for the opening-phrase subscribe, so
        we cancel and let the cancellation close the HTTP stream.

        Bounded by ``opening_phrase_join_timeout_ms`` so a misbehaving
        task can't stall the user's first turn indefinitely.
        """
        task = self._opening_phrase_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=self._opening_phrase_join_timeout_s
            )
        except asyncio.CancelledError:
            # Expected — the task we cancelled raised it on the way out.
            pass
        except asyncio.TimeoutError:
            logger.warning(
                "agent_core_llm.opening_phrase_join_timeout",
                extra={
                    "operation": "agent_core_llm._join_opening_phrase_task",
                    "status": "failure",
                    "call_sid": self._call_sid,
                    "session_id": self._session_id,
                    "timeout_ms": int(self._opening_phrase_join_timeout_s * 1000),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agent_core_llm.opening_phrase_join_error",
                extra={
                    "operation": "agent_core_llm._join_opening_phrase_task",
                    "status": "failure",
                    "call_sid": self._call_sid,
                    "session_id": self._session_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        finally:
            self._opening_phrase_task = None

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

        The acknowledgement is gated on a compound "bot actively speaking"
        signal (GH-203) — recency of the last TTS push *or* an in-flight SSE
        turn. This is more reliable than the original BotStartedSpeakingFrame /
        BotStoppedSpeakingFrame flag, which goes stale on the Vobiz pipeline
        because the stop-frame fires before audio finishes draining. Pipecat's
        UserTurnProcessor fires InterruptionFrame on every user-turn-start, so
        without the gate the phrase would play before every user turn.

        Called by the pipecat framework, not by user code.
        """
        await super()._start_interruption()
        active, gate_leg = self._bot_actively_speaking()
        self._interrupted = True
        logger.info(
            "agent_core_llm.interruption",
            extra={
                "operation": "agent_core_llm._start_interruption",
                "status": "success",
                "call_sid": self._call_sid,
                "session_id": self._session_id,
                "bot_actively_speaking": active,
                "gate_leg": gate_leg,
                "has_acknowledgement": bool(self._barge_in_acknowledgement),
            },
        )
        if active and self._barge_in_acknowledgement:
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

    async def _push_filler_after_delay(self, delay_s: float) -> None:
        """Push the configured filler phrase after ``delay_s`` (GH-205).

        Sleeps first; on cancellation (real sentence arrived, barge-in,
        end of turn) exits without pushing anything. Capped to one filler
        per turn by virtue of being scheduled exactly once per
        ``_handle_transcription_session`` invocation.

        Args:
            delay_s: Seconds to wait before pushing the filler.
        """
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            return
        # Re-check the gate at fire time — barge-in or a sentence that
        # arrived between the sleep wake-up and this point should still
        # suppress the filler.
        if self._interrupted:
            return
        await self.push_frame(TTSSpeakFrame(text=self._filler_phrase))
        logger.info(
            "agent_core_llm.filler_pushed",
            extra={
                "operation": "agent_core_llm._push_filler_after_delay",
                "status": "success",
                "call_sid": self._call_sid,
                "session_id": self._session_id,
                "delay_ms": int(delay_s * 1000),
            },
        )

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

        # GH-202: serialise the opening-phrase SSE consumer with the first
        # user turn so they don't both pull from the session's shared event
        # queue concurrently. No-op on subsequent turns (task already done).
        await self._join_opening_phrase_task()

        try:
            await self._channel.submit_input(
                self._session_id, frame.text, self._user_id or None
            )
            # GH-203: opening the SSE turn — barge-in gate's sse_active leg.
            self._sse_turn_active = True
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

        # GH-205: schedule the optional filler utterance. Cancelled as soon
        # as the first SentenceEvent reaches TTS so fast turns stay quiet.
        # GH-242: log every state transition so a missing filler is visible
        # in production (the previous build silently no-op'd because the
        # config never reached the voice service).
        filler_task: Optional[asyncio.Task] = None
        if self._filler_threshold_s is not None and self._filler_phrase:
            filler_task = asyncio.create_task(
                self._push_filler_after_delay(self._filler_threshold_s)
            )
            logger.info(
                "agent_core_llm.filler_armed",
                extra={
                    "operation": "agent_core_llm._handle_transcription_session",
                    "status": "success",
                    "delay_ms": int(self._filler_threshold_s * 1000),
                    "call_sid": self._call_sid,
                    "session_id": self._session_id,
                },
            )
        else:
            logger.info(
                "agent_core_llm.filler_skipped",
                extra={
                    "operation": "agent_core_llm._handle_transcription_session",
                    "status": "skipped",
                    "reason": (
                        "filler_threshold_ms unset"
                        if self._filler_threshold_s is None
                        else "filler_phrase empty"
                    ),
                    "call_sid": self._call_sid,
                    "session_id": self._session_id,
                },
            )

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
                        # GH-205: real sentence is here — cancel any pending
                        # filler so we don't speak both back-to-back.
                        if filler_task is not None and not filler_task.done():
                            filler_task.cancel()
                            logger.info(
                                "agent_core_llm.filler_cancelled",
                                extra={
                                    "operation": "agent_core_llm._handle_transcription_session",
                                    "status": "skipped",
                                    "reason": "first_sentence_arrived",
                                    "call_sid": self._call_sid,
                                    "session_id": self._session_id,
                                },
                            )
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
        finally:
            # GH-203: SSE turn is no longer in flight — close the sse_active
            # leg of the barge-in gate. Recency leg keeps the gate open for a
            # short window so audio still draining out of TTS continues to
            # count as "speaking" for any user interruption that lands during
            # that drain.
            self._sse_turn_active = False
            # GH-205: turn is over — cancel any still-pending filler and
            # absorb the resulting CancelledError so we don't leak a warning.
            if filler_task is not None and not filler_task.done():
                filler_task.cancel()
                try:
                    await filler_task
                except (asyncio.CancelledError, Exception):
                    pass

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
