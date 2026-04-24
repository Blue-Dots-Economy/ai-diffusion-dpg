"""
agent_core/turn_assembler.py

Multi-segment input assembler with configurable policy stack.
Sits between the HTTP server and AgentCore.stream_turn() for session-based channels.

Spec: docs/superpowers/specs/2026-04-14-agent-core-turn-assembler-spec.md
Issue: #72  Sub-tasks: #79, #80, #81, #82, #83

Design decisions NOT in the original spec (documented here for traceability):

1. SegmentInput dataclass: The spec defines add_segment(session_id, text) but
   stream_turn() needs channel, user_id, timestamp to build TurnInput. SegmentInput
   carries this metadata so TurnAssembler can construct TurnInput without a second
   HTTP call. First segment's metadata is cached on the buffer for subsequent segments.

2. context_bundle() on first segment: The semantic completeness gate needs NLU context
   (current_question, current_subagent_id) which comes from Memory Layer session state.
   We call async_memory.context_bundle() once on the first segment and cache it on
   SessionBuffer. This is a lightweight read that would happen anyway at stream_turn()
   start — we just pull it earlier to enable smarter assembly decisions.

3. Constructor dependencies: TurnAssembler takes nlu_processor, llm_wrapper, workflow,
   async_memory, and config alongside agent_core. These are needed for the semantic
   completeness gate which runs NLU classification before the full pipeline starts.

4. subscribe() resets buffer: After DoneEvent, subscribe() resets the buffer to WAITING
   instead of calling session_end(). This supports multi-turn sessions where the same
   SSE connection handles multiple turns. session_end() is only called on explicit
   disconnect or session cleanup.

5. Config placement: Turn assembler config lives in agent_core.yaml under
   reach_layer.turn_assembler (defaults) and reach_layer.channels.<name>.turn_assembler
   (per-channel overrides). Grouped under "reach_layer" as the top-level dict per lead
   direction, but still in agent_core.yaml because TurnAssembler is an Agent Core
   component. assembly_mode (session vs direct) stays in reach_layer.yaml — it's a
   Reach Layer routing concern, not an Agent Core concern.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from src.models import (
    ContextBundle,
    DoneEvent,
    SegmentInput,
    SentenceEvent,
    StreamEvent,
    TurnInput,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TurnStatus state machine
# ---------------------------------------------------------------------------


class TurnStatus(str, Enum):
    """State machine for a single turn's lifecycle within a session buffer.

    Transitions:
        WAITING → INVOKED      (policy triggered, lock acquired)
        WAITING → ABANDONED    (cancel() while waiting, or max_wait with no segments)
        INVOKED → COMPLETED    (DoneEvent emitted successfully)
        INVOKED → INTERRUPTED  (cancel() while LLM call in flight)
    """

    WAITING = "waiting"
    INVOKED = "invoked"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ABANDONED = "abandoned"


# ---------------------------------------------------------------------------
# SessionBuffer
# ---------------------------------------------------------------------------


@dataclass
class SessionBuffer:
    """Per-session state held in memory by TurnAssembler.

    Each active session has exactly one SessionBuffer. The buffer accumulates
    text segments, manages timer tasks, and holds the asyncio.Queue that bridges
    the invocation path to the SSE subscription.
    """

    session_id: str
    segments: list[str] = field(default_factory=list)
    status: TurnStatus = TurnStatus.WAITING
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    silence_task: Optional[asyncio.Task] = None
    ceiling_task: Optional[asyncio.Task] = None
    invocation_task: Optional[asyncio.Task] = None
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # Metadata from first segment / SSE subscribe — cached so subsequent
    # segments don't need it. channel is Optional so missing-channel stays
    # None all the way through the orchestrator (which raises a clear
    # Unsupported channel error) rather than silently defaulting to cli.
    channel: Optional[str] = None
    user_id: Optional[str] = None
    first_timestamp_ms: int = 0

    # Cached context_bundle from Memory Layer — fetched once on first add_segment()
    # so the semantic gate has NLU context (current_question, current_subagent_id).
    # Design decision #2: not in spec, but necessary for semantic gate to work.
    context_bundle: Optional[ContextBundle] = None
    _context_fetched: bool = False

    # Segments that arrived while this turn was INVOKED (barge-in).
    # After the current turn is cancelled and subscribe() resets the buffer,
    # these are replayed via add_segment() so the new turn starts automatically.
    pending_segments: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# TurnAssemblerBase ABC
# ---------------------------------------------------------------------------


class TurnAssemblerBase(ABC):
    """Abstract interface for turn assembly.

    All session-based channels route through this interface. Channel-specific
    behaviour is controlled by YAML config (silence thresholds, max wait ceilings).
    """

    @abstractmethod
    async def add_segment(self, session_id: str, segment: SegmentInput) -> None:
        """Accept a text segment for this session and evaluate policies.

        Args:
            session_id: Unique session identifier.
            segment: Text segment with metadata.
        """

    @abstractmethod
    async def subscribe(
        self, session_id: str, user_id: str | None = None
    ) -> AsyncGenerator[StreamEvent, None]:
        """Yield StreamEvents for this session until DoneEvent is received.

        Args:
            session_id: Unique session identifier.
            user_id: Optional user identifier. When provided on the first
                connect for a new session, triggers proactive emission of
                the entry subagent's opening_phrase (GH-149) before the
                event-drain loop begins.

        Yields:
            StreamEvent instances from the invocation pipeline.
        """
        yield  # pragma: no cover

    @abstractmethod
    async def cancel(self, session_id: str) -> None:
        """Interrupt the active or waiting turn for this session.

        Args:
            session_id: Unique session identifier.
        """

    @abstractmethod
    async def session_end(self, session_id: str) -> None:
        """Clean up all resources for a completed session.

        Args:
            session_id: Unique session identifier.
        """


# ---------------------------------------------------------------------------
# TurnAssembler concrete implementation
# ---------------------------------------------------------------------------


class TurnAssembler(TurnAssemblerBase):
    """In-memory turn assembler with configurable policy stack.

    Holds SessionBuffer instances keyed by session_id. Constructed with
    references to AgentCore and supporting components — injected at server startup.

    The policy stack is evaluated on each add_segment() call:
        1. Semantic completeness gate (NLU confidence check)
        2. Silence trigger (configurable timer, resets on each segment)
        3. Max wait ceiling (absolute timer, never resets)

    Config section: reach_layer.turn_assembler (defaults) and
    reach_layer.channels.<name>.turn_assembler (per-channel) in agent_core.yaml
    """

    def __init__(
        self,
        agent_core: Any,
        config: dict,
        nlu_processor: Any = None,
        llm_wrapper: Any = None,
        workflow: Any = None,
        async_memory: Any = None,
    ) -> None:
        """Initialise TurnAssembler with injected dependencies.

        Args:
            agent_core: AgentCore instance for calling stream_turn() directly.
            config: Full agent_core config dict. Turn assembler reads defaults from
                    config["reach_layer"]["turn_assembler"] and per-channel overrides
                    from config["reach_layer"]["channels"][<name>]["turn_assembler"].
            nlu_processor: NLUProcessor instance for semantic completeness gate.
            llm_wrapper: LLMWrapperBase instance for NLU LLM calls.
            workflow: AgentWorkflow instance for intent scoping.
            async_memory: AsyncMemoryLayerBase for fetching context_bundle on first segment.

        Raises:
            ValueError: If agent_core or config is None.
        """
        if agent_core is None:
            raise ValueError("agent_core must not be None")
        if config is None:
            raise ValueError("config must not be None")

        self._agent_core = agent_core
        self._config = config
        self._nlu_processor = nlu_processor
        self._llm = llm_wrapper
        self._workflow = workflow
        self._async_memory = async_memory

        # Defaults: reach_layer.turn_assembler (unchanged)
        rl_config: dict = (config or {}).get("reach_layer", {})
        ta_defaults: dict = rl_config.get("turn_assembler", {})

        # Hard-cut: reject legacy reach_layer.channels path (GH-137 migration).
        if rl_config.get("channels"):
            raise ValueError(
                "reach_layer.channels in agent_core config is removed — move per-channel "
                "turn_assembler to top-level channels.<name>.turn_assembler "
                "(see docs/superpowers/specs/2026-04-21-gh137-framework-uplift-design.md)"
            )

        self._default_config = {
            "semantic_gate": ta_defaults.get("semantic_gate", {
                "enabled": False,
                "confidence_threshold": 0.75,
            }),
            "silence_trigger": ta_defaults.get("silence_trigger", {
                "silence_ms": 400,
            }),
            "max_wait_ceiling": ta_defaults.get("max_wait_ceiling", {
                "max_wait_ms": 8000,
            }),
        }

        # Per-channel overrides now come from top-level channels.<name>.turn_assembler
        self._channels_config: dict = (config or {}).get("channels", {})

        self._sessions: dict[str, SessionBuffer] = {}

    # ------------------------------------------------------------------
    # Config resolution
    # ------------------------------------------------------------------

    def _resolve_config(self, channel: str) -> dict:
        """Resolve turn assembler config with per-channel overrides.

        Defaults come from reach_layer.turn_assembler. Per-channel overrides
        come from the top-level channels.<channel>.turn_assembler block
        (GH-137). This keeps the implementation domain-agnostic — all tuning
        is in YAML.

        Args:
            channel: Channel identifier (e.g. "voice", "web", "cli").

        Returns:
            Merged config dict for this channel.
        """
        base = {
            "semantic_gate": dict(self._default_config["semantic_gate"]),
            "silence_trigger": dict(self._default_config["silence_trigger"]),
            "max_wait_ceiling": dict(self._default_config["max_wait_ceiling"]),
        }
        # Per-channel overrides: reach_layer.channels.<channel>.turn_assembler
        channel_ta = self._channels_config.get(channel, {}).get("turn_assembler", {})
        for section in ("semantic_gate", "silence_trigger", "max_wait_ceiling"):
            if section in channel_ta:
                base[section].update(channel_ta[section])
        return base

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def add_segment(self, session_id: str, segment: SegmentInput) -> None:
        """Accept a text segment and evaluate the policy stack.

        Creates a new SessionBuffer if this is the first segment for the session.
        On first segment, also fetches context_bundle from Memory Layer for the
        semantic completeness gate.

        Args:
            session_id: Unique session identifier.
            segment: Text segment with metadata.
        """
        if not session_id:
            logger.warning(
                "turn_assembler.add_segment_empty_session",
                extra={"operation": "turn_assembler.add_segment", "status": "failure"},
            )
            return
        if not segment or not segment.text or not segment.text.strip():
            logger.warning(
                "turn_assembler.add_segment_empty_text",
                extra={
                    "operation": "turn_assembler.add_segment",
                    "status": "failure",
                    "session_id": session_id,
                },
            )
            return

        buffer = self._get_or_create_buffer(session_id, segment)

        # Barge-in: new segment arrived while a turn is in flight.
        # Cancel the current turn and queue this segment to be processed after reset.
        if buffer.status == TurnStatus.INVOKED:
            buffer.pending_segments.append(segment)
            logger.info(
                "turn_assembler.barge_in",
                extra={
                    "operation": "turn_assembler.add_segment",
                    "status": "success",
                    "session_id": session_id,
                    "reason": "new segment arrived while INVOKED — cancelling current turn",
                },
            )
            await self.cancel(session_id)
            return

        # Ignore segments when not in WAITING state (COMPLETED, INTERRUPTED, ABANDONED).
        if buffer.status != TurnStatus.WAITING:
            logger.info(
                "turn_assembler.segment_ignored",
                extra={
                    "operation": "turn_assembler.add_segment",
                    "status": "skipped",
                    "session_id": session_id,
                    "turn_status": buffer.status.value,
                    "reason": "buffer not in WAITING state",
                },
            )
            return

        buffer.segments.append(segment.text.strip())

        logger.info(
            "turn_assembler.segment_added",
            extra={
                "operation": "turn_assembler.add_segment",
                "status": "success",
                "session_id": session_id,
                "segment_count": len(buffer.segments),
            },
        )

        # Fetch context_bundle on first segment for semantic gate context
        if not buffer._context_fetched and self._async_memory:
            await self._fetch_context(buffer, segment)

        # Evaluate policy stack
        channel_config = self._resolve_config(buffer.channel)
        await self._evaluate_policies(session_id, buffer, channel_config)

    async def subscribe(self, session_id: str, user_id: str | None = None, channel: str | None = None,) -> AsyncGenerator[StreamEvent, None]:
        """Yield StreamEvents from the session's event queue until DoneEvent.

        Creates a buffer if one doesn't exist yet (subscribe can be called before
        the first segment arrives — the SSE connection opens at session start).

        When ``user_id`` is supplied and the session has no ``opening_phrase_emitted``
        flag in Memory Layer, pushes the entry subagent's opening_phrase as a
        SentenceEvent + DoneEvent pair onto the event queue before the drain loop
        begins (GH-149). The flag is persisted before events are enqueued so the
        orchestrator's first-turn gate won't re-emit on the next user turn.

        After DoneEvent is yielded, the buffer is reset to WAITING for the next
        turn in the same session. This supports multi-turn sessions over a single
        SSE connection (design decision #4).

        Args:
            session_id: Unique session identifier.
            user_id: Optional user identifier. Required for the proactive
                opening_phrase emission path; when None the emission is skipped
                (back-compat for callers that don't supply it).
            channel: Optional channel identifier ("voice", "web", "cli").
                When the reach-layer adapter supplies it at SSE subscribe
                time, the session buffer is created with the correct channel
                from birth so per-channel config (system_prompt_suffix,
                tts_rules, turn_assembler timing) resolves correctly even
                when subscribe() runs before the first add_segment().

        Yields:
            StreamEvent instances until DoneEvent is received.
        """
        if not session_id:
            return

        if session_id not in self._sessions:
            self._sessions[session_id] = SessionBuffer( session_id=session_id, channel=channel, user_id=user_id,)

        buffer = self._sessions[session_id]

        # GH-149: proactively emit the entry subagent's opening_phrase on the
        # first SSE connect for a brand-new session, so voice/cli/web channels
        # don't need their own static greeting. Idempotent across reconnects
        # via the persisted session.opening_phrase_emitted flag.
        await self._emit_opening_phrase_if_first(session_id, user_id, buffer)

        while True:
            event = await buffer.event_queue.get()
            yield event
            if isinstance(event, DoneEvent):
                # Reset buffer in-place for the next turn so the same SSE
                # connection stays open. Design decision #4: single persistent
                # connection per session; session_end() handles full cleanup.
                if session_id in self._sessions:
                    pending = list(buffer.pending_segments)
                    # GH-152 Phase 2: on INTERRUPTED, discard the original
                    # segments. An "interrupted" status only fires when the
                    # user barges in during INVOKED state — the LLM was
                    # already running on the original input and has produced
                    # (partial) output the user is now reacting to. Replaying
                    # the original alongside the correction produces noisy
                    # prompts like "I want electrician jobs wait that's wrong,
                    # I said plumber" that mislead NLU / routing. The
                    # user-visible contract is "whatever you say during bot
                    # TTS replaces, not extends, your prior turn."
                    #
                    # For COMPLETED or ABANDONED turns we also don't replay —
                    # COMPLETED processed all segments, ABANDONED dropped
                    # them on purpose.
                    if (
                        event.turn_status == "interrupted"
                        and buffer.segments
                    ):
                        logger.info(
                            "turn_assembler.barge_in_discarded_segments",
                            extra={
                                "operation": "turn_assembler.subscribe",
                                "status": "success",
                                "session_id": session_id,
                                "discarded_count": len(buffer.segments),
                                "pending_count": len(pending),
                            },
                        )
                    self._reset_buffer(buffer)
                    # Replay only the barge-in (pending) segments.
                    for seg in pending:
                        await self.add_segment(session_id, seg)

    async def _emit_opening_phrase_if_first(
        self,
        session_id: str,
        user_id: str | None,
        buffer: SessionBuffer,
    ) -> None:
        """Push the entry subagent's opening_phrase onto the session queue once.

        Runs at SSE subscribe time so session-mode channels receive the
        welcome utterance without waiting for the user to speak first
        (GH-149). Gated on the persisted ``session.opening_phrase_emitted``
        flag so reconnects don't re-emit, and so the orchestrator's
        first-turn gate (`orchestrator.py`) skips once the flag is set.

        No-ops when ``user_id`` is None, when workflow/async_memory are not
        wired, when the flag is already set, or when the start subagent's
        opening_phrase is empty. Memory write is awaited *before* events
        are pushed to keep the flag durable by the time the channel plays
        the utterance.

        Does not run a Trust Layer output check — the phrase is
        config-authored text, not LLM output. This mirrors the orchestrator's
        existing short-circuit for opening_phrase emissions.

        Args:
            session_id: Unique session identifier.
            user_id: User identifier; required to read and write session state.
            buffer: Session buffer whose event_queue receives the events.
        """
        if not user_id:
            return
        if self._workflow is None or self._async_memory is None:
            return

        try:
            bundle = await self._async_memory.context_bundle(session_id, user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "turn_assembler.opening_phrase_context_bundle_failed",
                extra={
                    "operation": "turn_assembler._emit_opening_phrase_if_first",
                    "status": "skipped",
                    "session_id": session_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return

        if bundle.session.get("opening_phrase_emitted"):
            return

        # Prefer any subagent already persisted on the session; otherwise
        # fall back to the workflow's declared start subagent.
        current_subagent_id = (
            bundle.session.get("current_subagent_id")
            or getattr(self._workflow, "start_subagent_id", "")
        )
        if not current_subagent_id:
            return

        subagent = getattr(self._workflow, "subagents", {}).get(current_subagent_id)
        opening_phrase = (getattr(subagent, "opening_phrase", "") or "").strip()

        # Always set the flag first so a fast user turn can't race the orchestrator
        # gate into re-emitting the same phrase. Write completes before any events
        # are enqueued.
        try:
            await self._async_memory.write(
                session_id, user_id, "session", "opening_phrase_emitted", True
            )
            await self._async_memory.write(
                session_id, user_id, "session", "current_subagent_id", current_subagent_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "turn_assembler.opening_phrase_flag_write_failed",
                extra={
                    "operation": "turn_assembler._emit_opening_phrase_if_first",
                    "status": "skipped",
                    "session_id": session_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return

        if not opening_phrase:
            logger.info(
                "turn_assembler.opening_phrase_empty",
                extra={
                    "operation": "turn_assembler._emit_opening_phrase_if_first",
                    "status": "skipped",
                    "session_id": session_id,
                    "subagent_id": current_subagent_id,
                },
            )
            return

        await buffer.event_queue.put(SentenceEvent(text=opening_phrase, sentence_index=0))
        await buffer.event_queue.put(DoneEvent(turn_status="completed"))

        logger.info(
            "turn_assembler.opening_phrase_emitted",
            extra={
                "operation": "turn_assembler._emit_opening_phrase_if_first",
                "status": "emitted",
                "session_id": session_id,
                "subagent_id": current_subagent_id,
            },
        )

    async def cancel(self, session_id: str) -> None:
        """Interrupt the active or waiting turn for this session.

        If INVOKED: cancels the invocation task, transitions to INTERRUPTED,
        pushes DoneEvent with turn_status="interrupted".
        If WAITING: transitions to ABANDONED, pushes DoneEvent with turn_status="abandoned".
        Other states: no-op.

        Memory consistency (#83): If stream_turn() is in flight, the async memory
        write task (_schedule_flush) is cancelled when stream_turn() is interrupted.
        No partial writes occur. The next turn re-reads the same state.

        Args:
            session_id: Unique session identifier.
        """
        if session_id not in self._sessions:
            return

        buffer = self._sessions[session_id]

        async with buffer._lock:
            if buffer.status == TurnStatus.INVOKED:
                buffer.status = TurnStatus.INTERRUPTED
                self._cancel_all_tasks(buffer)
                await buffer.event_queue.put(
                    DoneEvent(turn_status="interrupted")
                )
                logger.info(
                    "turn_assembler.cancel_interrupted",
                    extra={
                        "operation": "turn_assembler.cancel",
                        "status": "success",
                        "session_id": session_id,
                        "turn_status": "interrupted",
                    },
                )

            elif buffer.status == TurnStatus.WAITING:
                buffer.status = TurnStatus.ABANDONED
                self._cancel_all_tasks(buffer)
                await buffer.event_queue.put(
                    DoneEvent(turn_status="abandoned")
                )
                logger.info(
                    "turn_assembler.cancel_abandoned",
                    extra={
                        "operation": "turn_assembler.cancel",
                        "status": "success",
                        "session_id": session_id,
                        "turn_status": "abandoned",
                    },
                )

    async def session_end(self, session_id: str) -> None:
        """Clean up all resources for a completed session.

        Cancels all pending tasks and removes the buffer from memory.

        Args:
            session_id: Unique session identifier.
        """
        if session_id not in self._sessions:
            return

        buffer = self._sessions.pop(session_id)
        self._cancel_all_tasks(buffer)
        logger.info(
            "turn_assembler.session_end",
            extra={
                "operation": "turn_assembler.session_end",
                "status": "success",
                "session_id": session_id,
            },
        )

    # ------------------------------------------------------------------
    # Policy stack evaluation
    # ------------------------------------------------------------------

    async def _evaluate_policies(
        self, session_id: str, buffer: SessionBuffer, config: dict
    ) -> None:
        """Evaluate the policy stack in order after each add_segment().

        Policy order (spec-defined):
            1. Semantic completeness gate — if NLU confidence >= threshold, invoke immediately
            2. Silence trigger — timer that resets on each segment
            3. Max wait ceiling — absolute timer, never resets

        If semantic gate triggers, silence timer is not started.
        If both timers fire simultaneously, only the first to acquire the lock transitions.

        Args:
            session_id: Session identifier.
            buffer: The session's buffer.
            config: Resolved per-channel config.
        """
        # Policy 1: Semantic completeness gate
        gate_config = config.get("semantic_gate", {})
        if gate_config.get("enabled", False):
            triggered = await self._semantic_gate(session_id, buffer, gate_config)
            if triggered:
                return  # Invoked — skip timers

        # Policy 2: Silence trigger — reset on every segment
        silence_config = config.get("silence_trigger", {})
        silence_ms = silence_config.get("silence_ms", 400)

        # Cancel existing silence timer and restart
        if buffer.silence_task and not buffer.silence_task.done():
            buffer.silence_task.cancel()

        buffer.silence_task = asyncio.create_task(
            self._silence_timer(session_id, silence_ms)
        )

        # Policy 3: Max wait ceiling — started once, never reset
        ceiling_config = config.get("max_wait_ceiling", {})
        max_wait_ms = ceiling_config.get("max_wait_ms", 8000)

        if buffer.ceiling_task is None:
            buffer.ceiling_task = asyncio.create_task(
                self._ceiling_timer(session_id, max_wait_ms)
            )

    async def _semantic_gate(
        self, session_id: str, buffer: SessionBuffer, gate_config: dict
    ) -> bool:
        """Evaluate semantic completeness using NLU classification.

        Runs the NLU processor on the assembled text. If confidence >= threshold
        and intent is not "unknown", acquires the lock and triggers invocation.

        If NLU call fails: logs error and falls through (never blocks on infra failure).

        Args:
            session_id: Session identifier.
            buffer: The session's buffer.
            gate_config: Semantic gate config with confidence_threshold.

        Returns:
            True if invocation was triggered, False to fall through to timers.
        """
        if not self._nlu_processor or not self._llm:
            return False

        threshold = gate_config.get("confidence_threshold", 0.75)
        assembled_text = " ".join(buffer.segments)

        try:
            # Get NLU context from cached context_bundle
            current_question = ""
            current_subagent_id = ""
            allowed_intents = None

            if buffer.context_bundle:
                session_data = buffer.context_bundle.session or {}
                current_question = session_data.get("current_question", "")
                current_subagent_id = session_data.get(
                    "current_subagent_id",
                    self._workflow.start_subagent_id if self._workflow else "",
                )

                # Scope intents to current subagent (same as orchestrator)
                if self._workflow and current_subagent_id in self._workflow.subagents:
                    subagent = self._workflow.subagents[current_subagent_id]
                    allowed_intents = list(subagent.valid_intents or [])
                    if self._workflow.global_intents:
                        allowed_intents.extend(self._workflow.global_intents)

            start = time.time()
            nlu_result = self._nlu_processor.process(
                normalised_input=assembled_text,
                current_question=current_question,
                current_subagent_id=current_subagent_id,
                llm=self._llm,
                allowed_intents=allowed_intents,
            )

            logger.info(
                "turn_assembler.semantic_gate",
                extra={
                    "operation": "turn_assembler.semantic_gate",
                    "status": "success",
                    "session_id": session_id,
                    "intent": nlu_result.intent,
                    "confidence": nlu_result.confidence,
                    "threshold": threshold,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

            if nlu_result.confidence >= threshold and nlu_result.intent != "unknown":
                async with buffer._lock:
                    if buffer.status != TurnStatus.WAITING:
                        return False
                    buffer.status = TurnStatus.INVOKED
                    self._cancel_timer_tasks(buffer)
                    buffer.invocation_task = asyncio.create_task(
                        self._invoke(session_id)
                    )
                return True

        except Exception as e:
            # Spec: NLU infra failure → log, fall through — never block on NLU failure
            logger.warning(
                "turn_assembler.semantic_gate_error",
                extra={
                    "operation": "turn_assembler.semantic_gate",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

        return False

    async def _silence_timer(self, session_id: str, silence_ms: int) -> None:
        """Sleep for silence_ms then trigger invocation if still WAITING.

        Started on first segment, reset (cancel + restart) on every subsequent
        add_segment(). If the task fires and status is WAITING, acquires the lock
        and transitions to INVOKED.

        Args:
            session_id: Session identifier.
            silence_ms: Silence duration in milliseconds.
        """
        try:
            await asyncio.sleep(silence_ms / 1000.0)
        except asyncio.CancelledError:
            return

        if session_id not in self._sessions:
            return

        buffer = self._sessions[session_id]
        async with buffer._lock:
            if buffer.status != TurnStatus.WAITING:
                return
            if not buffer.segments:
                return  # No segments accumulated — nothing to invoke

            buffer.status = TurnStatus.INVOKED
            self._cancel_timer_tasks(buffer)
            buffer.invocation_task = asyncio.create_task(
                self._invoke(session_id)
            )

            logger.info(
                "turn_assembler.silence_trigger_fired",
                extra={
                    "operation": "turn_assembler.silence_trigger",
                    "status": "success",
                    "session_id": session_id,
                    "segment_count": len(buffer.segments),
                    "silence_ms": silence_ms,
                },
            )

    async def _ceiling_timer(self, session_id: str, max_wait_ms: int) -> None:
        """Absolute timer that fires once after max_wait_ms. Never reset.

        If status is still WAITING when this fires, acquires lock and triggers.
        If already INVOKED, this is a no-op.

        Args:
            session_id: Session identifier.
            max_wait_ms: Maximum wait in milliseconds.
        """
        try:
            await asyncio.sleep(max_wait_ms / 1000.0)
        except asyncio.CancelledError:
            return

        if session_id not in self._sessions:
            return

        buffer = self._sessions[session_id]
        async with buffer._lock:
            if buffer.status != TurnStatus.WAITING:
                return  # Already invoked or cancelled — no-op

            if not buffer.segments:
                # Max wait ceiling fired with no segments → ABANDONED (spec)
                buffer.status = TurnStatus.ABANDONED
                await buffer.event_queue.put(
                    DoneEvent(turn_status="abandoned")
                )
                logger.info(
                    "turn_assembler.ceiling_abandoned",
                    extra={
                        "operation": "turn_assembler.ceiling_timer",
                        "status": "success",
                        "session_id": session_id,
                        "turn_status": "abandoned",
                        "max_wait_ms": max_wait_ms,
                    },
                )
                return

            buffer.status = TurnStatus.INVOKED
            self._cancel_timer_tasks(buffer)
            buffer.invocation_task = asyncio.create_task(
                self._invoke(session_id)
            )

            logger.info(
                "turn_assembler.ceiling_trigger_fired",
                extra={
                    "operation": "turn_assembler.ceiling_timer",
                    "status": "success",
                    "session_id": session_id,
                    "segment_count": len(buffer.segments),
                    "max_wait_ms": max_wait_ms,
                },
            )

    # ------------------------------------------------------------------
    # Invocation path (no HTTP hop — spec requirement)
    # ------------------------------------------------------------------

    async def _invoke(self, session_id: str) -> None:
        """Assemble segments and call agent_core.stream_turn() directly.

        Per spec: TurnAssembler calls stream_turn() as a Python method — in-process,
        no HTTP, no serialisation. StreamEvents are pushed into the SessionBuffer's
        event_queue. The open GET /sessions/{id}/events connection drains the queue.

        Args:
            session_id: Session identifier.
        """
        if session_id not in self._sessions:
            return

        buffer = self._sessions[session_id]
        assembled_text = " ".join(buffer.segments)

        turn_input = TurnInput(
            session_id=session_id,
            user_message=assembled_text,
            channel=buffer.channel,
            timestamp_ms=buffer.first_timestamp_ms or int(time.time() * 1000),
            user_id=buffer.user_id,
        )

        logger.info(
            "turn_assembler.invoke_start",
            extra={
                "operation": "turn_assembler.invoke",
                "status": "success",
                "session_id": session_id,
                "segment_count": len(buffer.segments),
                "assembled_length": len(assembled_text),
            },
        )

        start = time.time()
        try:
            async for event in self._agent_core.stream_turn(turn_input):
                await buffer.event_queue.put(event)
                if isinstance(event, DoneEvent):
                    buffer.status = TurnStatus.COMPLETED
                    break
        except asyncio.CancelledError:
            # Invocation was cancelled (barge-in / cancel()) — status already set
            # by cancel(). Memory consistency (#83): async memory write task in
            # stream_turn() is cancelled automatically when the task is cancelled.
            logger.info(
                "turn_assembler.invoke_cancelled",
                extra={
                    "operation": "turn_assembler.invoke",
                    "status": "failure",
                    "session_id": session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            logger.error(
                "turn_assembler.invoke_error",
                extra={
                    "operation": "turn_assembler.invoke",
                    "status": "failure",
                    "session_id": session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            # Push a DoneEvent so the subscriber doesn't hang
            buffer.status = TurnStatus.COMPLETED
            await buffer.event_queue.put(
                DoneEvent(
                    turn_status="abandoned",
                    latency_ms=int((time.time() - start) * 1000),
                )
            )

    # ------------------------------------------------------------------
    # Context fetching
    # ------------------------------------------------------------------

    async def _fetch_context(self, buffer: SessionBuffer, segment: SegmentInput) -> None:
        """Fetch context_bundle from Memory Layer on first segment.

        Design decision #2: The semantic gate needs current_question and
        current_subagent_id from session state. We fetch this once and cache it.
        If the fetch fails, the semantic gate falls through to timers gracefully.

        Args:
            buffer: The session's buffer.
            segment: The current segment (for user_id).
        """
        buffer._context_fetched = True
        try:
            user_id = segment.user_id or buffer.session_id
            start = time.time()
            bundle = await self._async_memory.context_bundle(
                buffer.session_id, user_id
            )
            buffer.context_bundle = bundle
            logger.info(
                "turn_assembler.context_fetched",
                extra={
                    "operation": "turn_assembler.fetch_context",
                    "status": "success",
                    "session_id": buffer.session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
        except Exception as e:
            # Context fetch failure is non-fatal — semantic gate will work
            # without context, just with less accuracy. Timers still function.
            logger.warning(
                "turn_assembler.context_fetch_error",
                extra={
                    "operation": "turn_assembler.fetch_context",
                    "status": "failure",
                    "session_id": buffer.session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    # ------------------------------------------------------------------
    # Buffer management helpers
    # ------------------------------------------------------------------

    def _get_or_create_buffer(
        self, session_id: str, segment: SegmentInput
    ) -> SessionBuffer:
        """Get existing buffer or create a new one for this session.

        On creation, caches metadata from the first segment (channel, user_id,
        timestamp) so subsequent segments don't need to carry it.

        Args:
            session_id: Session identifier.
            segment: Current segment with metadata.

        Returns:
            The session's buffer.
        """
        if session_id not in self._sessions:
            buffer = SessionBuffer(
                session_id=session_id,
                channel=segment.channel,
                user_id=segment.user_id,
                first_timestamp_ms=segment.timestamp_ms or int(time.time() * 1000),
            )
            self._sessions[session_id] = buffer
            return buffer

        return self._sessions[session_id]

    def _reset_buffer(self, buffer: SessionBuffer) -> None:
        """Reset a buffer to WAITING state for the next turn.

        Preserves session_id, channel, user_id, and context_bundle.
        Clears segments, resets status, creates a new event queue.

        Args:
            buffer: The buffer to reset.
        """
        self._cancel_all_tasks(buffer)
        buffer.segments = []
        buffer.pending_segments = []
        buffer.status = TurnStatus.WAITING
        buffer.event_queue = asyncio.Queue()
        buffer.created_at_ms = int(time.time() * 1000)

    def _cancel_all_tasks(self, buffer: SessionBuffer) -> None:
        """Cancel all asyncio tasks on a buffer.

        Args:
            buffer: The buffer whose tasks to cancel.
        """
        self._cancel_timer_tasks(buffer)
        if buffer.invocation_task and not buffer.invocation_task.done():
            buffer.invocation_task.cancel()
            buffer.invocation_task = None

    def _cancel_timer_tasks(self, buffer: SessionBuffer) -> None:
        """Cancel only timer tasks (silence + ceiling), not the invocation task.

        Args:
            buffer: The buffer whose timers to cancel.
        """
        if buffer.silence_task and not buffer.silence_task.done():
            buffer.silence_task.cancel()
            buffer.silence_task = None
        if buffer.ceiling_task and not buffer.ceiling_task.done():
            buffer.ceiling_task.cancel()
            buffer.ceiling_task = None
