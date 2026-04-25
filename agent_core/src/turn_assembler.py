"""
agent_core/turn_assembler.py

Multi-segment input assembler with configurable policy stack.
Sits between the HTTP server and AgentCore.stream_turn() for session-based channels.

Spec: docs/superpowers/specs/2026-04-14-agent-core-turn-assembler-spec.md
Issue: #72  Sub-tasks: #79, #80, #81, #82, #83
Refactor: #224 — TurnAssembler now uses Session/Turn for all per-session state.

Design decisions NOT in the original spec (documented here for traceability):

1. SegmentInput dataclass: The spec defines add_segment(session_id, text) but
   stream_turn() needs channel, user_id, timestamp to build TurnInput. SegmentInput
   carries this metadata so TurnAssembler can construct TurnInput without a second
   HTTP call. First segment's metadata is cached on the Session for subsequent segments.

2. context_bundle() on first segment: The semantic completeness gate needs NLU context
   (current_question, current_subagent_id) which comes from Memory Layer session state.
   We call async_memory.context_bundle() once on the first segment and cache it on
   the Turn. This is a lightweight read that would happen anyway at stream_turn()
   start — we just pull it earlier to enable smarter assembly decisions.

3. Constructor dependencies: TurnAssembler takes nlu_processor, llm_wrapper, workflow,
   async_memory, and config alongside agent_core. These are needed for the semantic
   completeness gate which runs NLU classification before the full pipeline starts.

4. subscribe() rolls over across turns: After DoneEvent, subscribe() waits for
   session.turn_changed to learn when a new Turn becomes current. This supports
   multi-turn sessions where the same SSE connection handles multiple turns.
   session_end() is only called on explicit disconnect or session cleanup.

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
from typing import Any, Optional

from src.models import (
    ContextBundle,
    DoneEvent,
    SegmentInput,
    SentenceEvent,
    StreamEvent,
    TurnInput,
)
from .turn import Turn, TurnStatus
from .session import Session

logger = logging.getLogger(__name__)


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
        self,
        session_id: str,
        user_id: str | None = None,
        channel: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Yield StreamEvents for this session until DoneEvent is received.

        Args:
            session_id: Unique session identifier.
            user_id: Optional user identifier. When provided on the first
                connect for a new session, triggers proactive emission of
                the entry subagent's opening_phrase (GH-149) before the
                event-drain loop begins.
            channel: Optional channel identifier ("voice", "web", "cli").
                When the reach-layer adapter supplies it at SSE subscribe
                time, the session is created with the correct channel from
                birth so per-channel config resolves correctly even when
                subscribe() runs before the first add_segment().

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

    Holds Session instances keyed by session_id. Constructed with references
    to AgentCore and supporting components — injected at server startup.

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

        self._sessions: dict[str, Session] = {}

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
        channel_ta = self._channels_config.get(channel or "", {}).get("turn_assembler", {})
        for section in ("semantic_gate", "silence_trigger", "max_wait_ceiling"):
            if section in channel_ta:
                base[section].update(channel_ta[section])
        return base

    # ------------------------------------------------------------------
    # Session management helpers
    # ------------------------------------------------------------------

    def _get_or_create_session(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        channel: str | None = None,
    ) -> Session:
        """Look up the Session for session_id, creating it on first access.

        Args:
            session_id: Unique session identifier.
            user_id: User identifier (cached on first access; ignored thereafter).
            channel: Channel name (cached on first access; ignored thereafter).

        Returns:
            The Session — existing or newly created.
        """
        session = self._sessions.get(session_id)
        if session is None:
            session = Session(
                session_id=session_id,
                user_id=user_id,
                channel=channel or "",
            )
            self._sessions[session_id] = session
        return session

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def add_segment(self, session_id: str, segment: SegmentInput) -> None:
        """Accept a text segment and evaluate the policy stack.

        Creates a new Session (and first Turn) if this is the first segment for
        the session. On first segment, also fetches context_bundle from Memory
        Layer for the semantic completeness gate.

        If the current turn is already invoked (barge-in), sets the abort signal
        on the current turn and installs a new Turn with the barge-in segment.

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

        session = self._get_or_create_session(
            session_id,
            user_id=getattr(segment, "user_id", None),
            channel=getattr(segment, "channel", None),
        )

        async with session._lock:
            turn = session.current_turn

            # Barge-in: new segment arrived while a turn is in flight.
            if turn is not None and turn.status == TurnStatus.INVOKED:
                logger.info(
                    "turn_assembler.cancel_and_fold",
                    extra={
                        "operation": "turn_assembler.cancel_and_fold",
                        "status": "success",
                        "session_id": session_id,
                        "cancelled_turn_id": turn.turn_id,
                        "folded_segment_count": 1,
                        "reason": "new segment arrived while INVOKED — aborting current turn",
                    },
                )
                # Signal the producer and mark INTERRUPTED before installing new turn.
                turn.status = TurnStatus.INTERRUPTED
                turn.abort_event.set()
                for task in (turn.invocation_task, turn.silence_task, turn.ceiling_task):
                    if task is not None and not task.done():
                        task.cancel()
                await turn.event_queue.put(
                    DoneEvent(turn_status="interrupted", turn_id=turn.turn_id)
                )
                # Install a fresh turn with the barge-in segment pre-loaded.
                new_turn = await session.replace_turn(seed_segments=[segment])
                turn = new_turn

            elif turn is None or turn.status in (
                TurnStatus.COMPLETED,
                TurnStatus.INTERRUPTED,
                TurnStatus.ABANDONED,
            ):
                # First segment or post-terminal: install a fresh Turn.
                turn = await session.replace_turn(seed_segments=[])
                turn.segments.append(segment)
            else:
                # Still WAITING — just append.
                turn.segments.append(segment)

            logger.info(
                "turn_assembler.segment_added",
                extra={
                    "operation": "turn_assembler.add_segment",
                    "status": "success",
                    "session_id": session_id,
                    "turn_id": turn.turn_id,
                    "segment_count": len(turn.segments),
                },
            )

        # Outside the lock: cache context on first segment for the semantic gate.
        if not turn._context_fetched and self._async_memory:
            await self._fetch_context(turn, segment)

        # Evaluate policy stack.
        channel_config = self._resolve_config(turn.channel)
        await self._evaluate_policies(session_id, turn, channel_config)

    async def subscribe(
        self,
        session_id: str,
        user_id: str | None = None,
        channel: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Yield StreamEvents for this session across multiple turns.

        Holds a long-lived connection: drains the current Turn's queue until
        DoneEvent, then awaits session.turn_changed for the next Turn to become
        current. Exits when session.ended is True.

        When ``user_id`` is supplied and the session has no
        ``opening_phrase_emitted`` flag in Memory Layer, pushes the entry
        subagent's opening_phrase as a SentenceEvent + DoneEvent pair before
        the drain loop begins (GH-149).

        Args:
            session_id: Unique session identifier.
            user_id: Optional user identifier. Required for the proactive
                opening_phrase emission path; when None the emission is skipped
                (back-compat for callers that don't supply it).
            channel: Optional channel identifier ("voice", "web", "cli").
                When the reach-layer adapter supplies it at SSE subscribe
                time, the session is created with the correct channel from
                birth so per-channel config resolves correctly even when
                subscribe() runs before the first add_segment().

        Yields:
            StreamEvent instances from each turn in turn order.
        """
        if not session_id:
            return

        session = self._get_or_create_session(session_id, user_id=user_id, channel=channel)

        # GH-149: proactively emit the entry subagent's opening_phrase.
        await self._emit_opening_phrase_if_first(session_id, user_id, session)

        seen: Optional[Turn] = None
        while not session.ended:
            session.turn_changed.clear()
            turn = session.current_turn
            if turn is None or turn is seen:
                await session.turn_changed.wait()
                continue
            async for event in turn.iter_events():
                yield event
            seen = turn

    async def _emit_opening_phrase_if_first(
        self,
        session_id: str,
        user_id: str | None,
        session: Session,
    ) -> None:
        """Push the entry subagent's opening_phrase onto the session queue once.

        Runs at SSE subscribe time so session-mode channels receive the
        welcome utterance without waiting for the user to speak first
        (GH-149). Gated on the persisted ``session.opening_phrase_emitted``
        flag so reconnects don't re-emit.

        No-ops when ``user_id`` is None, when workflow/async_memory are not
        wired, when the flag is already set, or when the start subagent's
        opening_phrase is empty.

        Args:
            session_id: Unique session identifier.
            user_id: User identifier; required to read and write session state.
            session: The Session whose current_turn receives the events.
        """
        if not user_id:
            return
        if self._workflow is None or self._async_memory is None:
            return

        try:
            t_bundle_start = time.time()
            bundle = await self._async_memory.context_bundle(session_id, user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "turn_assembler.opening_phrase_context_bundle_failed",
                extra={
                    "operation": "turn_assembler._emit_opening_phrase_if_first",
                    "status": "skipped",
                    "session_id": session_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": int((time.time() - t_bundle_start) * 1000),
                },
            )
            return

        if bundle.session.get("opening_phrase_emitted"):
            logger.info(
                "turn_assembler.opening_phrase_already_emitted",
                extra={
                    "operation": "turn_assembler._emit_opening_phrase_if_first",
                    "status": "skipped",
                    "reason": "opening_phrase_emitted flag already set",
                    "session_id": session_id,
                },
            )
            return

        # GH-201: when consent is required and not yet granted, suppress the
        # opening phrase. The orchestrator emits it on the first post-consent
        # turn so the caller doesn't hear two utterances back-to-back at session
        # start. Flag is intentionally left unset here.
        ask_for_consent: bool = self._config.get("agent", {}).get("ask_for_consent", False)
        if ask_for_consent and bundle.session.get("user_storage_mode") is None:
            logger.info(
                "turn_assembler.opening_phrase_suppressed_pending_consent",
                extra={
                    "operation": "turn_assembler._emit_opening_phrase_if_first",
                    "status": "skipped",
                    "reason": "ask_for_consent=true and consent not yet granted",
                    "session_id": session_id,
                },
            )
            return

        current_subagent_id = (
            bundle.session.get("current_subagent_id")
            or getattr(self._workflow, "start_subagent_id", "")
        )
        if not current_subagent_id:
            logger.warning(
                "turn_assembler.opening_phrase_no_subagent",
                extra={
                    "operation": "turn_assembler._emit_opening_phrase_if_first",
                    "status": "skipped",
                    "reason": "no current_subagent_id resolved",
                    "session_id": session_id,
                    "workflow_loaded": self._workflow is not None,
                },
            )
            return

        subagent = getattr(self._workflow, "subagents", {}).get(current_subagent_id)
        opening_phrase = (getattr(subagent, "opening_phrase", "") or "").strip()

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

        # Install a dedicated opening-phrase turn and seal it immediately.
        async with session._lock:
            op_turn = await session.replace_turn(seed_segments=[])
            op_turn.status = TurnStatus.INVOKED
            await op_turn.event_queue.put(SentenceEvent(text=opening_phrase, sentence_index=0))
            await op_turn.event_queue.put(DoneEvent(turn_status="completed"))
            op_turn.status = TurnStatus.COMPLETED

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

        Idempotent: if the current turn is already terminal, no-op.
        Sets abort_event, cancels the invocation/timer tasks, and seals the
        turn's queue with a terminal DoneEvent. The producer (_invoke /
        stream_turn / claude_wrapper) sees abort_event before every
        yield/put and exits without enqueuing further events.

        Args:
            session_id: Unique session identifier.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        async with session._lock:
            turn = session.current_turn
            if turn is None or turn.status not in (TurnStatus.WAITING, TurnStatus.INVOKED):
                return
            new_status = (
                TurnStatus.INTERRUPTED
                if turn.status == TurnStatus.INVOKED
                else TurnStatus.ABANDONED
            )
            turn.status = new_status
            turn.abort_event.set()
            for task in (turn.invocation_task, turn.silence_task, turn.ceiling_task):
                if task is not None and not task.done():
                    task.cancel()
            await turn.event_queue.put(
                DoneEvent(
                    turn_status=new_status.value,
                    turn_id=turn.turn_id,
                )
            )
            logger.info(
                "turn_assembler.cancel",
                extra={
                    "operation": "turn_assembler.cancel",
                    "status": "success",
                    "session_id": session_id,
                    "turn_id": turn.turn_id,
                    "epoch": turn.epoch,
                    "turn_status": new_status.value,
                },
            )

    async def session_end(self, session_id: str) -> None:
        """Clean up all resources for a completed session.

        Cancels the active turn (if any), marks the session ended, and
        signals subscribers to exit their iteration loop.

        Args:
            session_id: Unique session identifier.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        await self.cancel(session_id)  # idempotent
        session.ended = True
        session.turn_changed.set()  # wake any blocked subscriber
        self._sessions.pop(session_id, None)
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
        self, session_id: str, turn: Turn, config: dict
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
            turn: The current Turn.
            config: Resolved per-channel config.
        """
        # Policy 1: Semantic completeness gate
        gate_config = config.get("semantic_gate", {})
        if gate_config.get("enabled", False):
            triggered = await self._semantic_gate(session_id, turn, gate_config)
            if triggered:
                return  # Invoked — skip timers

        # Policy 2: Silence trigger — reset on every segment
        silence_config = config.get("silence_trigger", {})
        silence_ms = silence_config.get("silence_ms", 400)

        # Cancel existing silence timer and restart
        if turn.silence_task and not turn.silence_task.done():
            turn.silence_task.cancel()

        turn.silence_task = asyncio.create_task(
            self._silence_timer(session_id, silence_ms)
        )

        # Policy 3: Max wait ceiling — started once, never reset
        ceiling_config = config.get("max_wait_ceiling", {})
        max_wait_ms = ceiling_config.get("max_wait_ms", 8000)

        if turn.ceiling_task is None:
            turn.ceiling_task = asyncio.create_task(
                self._ceiling_timer(session_id, max_wait_ms)
            )

    async def _semantic_gate(
        self, session_id: str, turn: Turn, gate_config: dict
    ) -> bool:
        """Evaluate semantic completeness using NLU classification.

        Runs the NLU processor on the assembled text. If confidence >= threshold
        and intent is not "unknown", acquires the lock and triggers invocation.

        If NLU call fails: logs error and falls through (never blocks on infra failure).

        Args:
            session_id: Session identifier.
            turn: The current Turn.
            gate_config: Semantic gate config with confidence_threshold.

        Returns:
            True if invocation was triggered, False to fall through to timers.
        """
        if not self._nlu_processor or not self._llm:
            return False

        threshold = gate_config.get("confidence_threshold", 0.75)
        assembled_text = " ".join(s.text.strip() for s in turn.segments)

        try:
            # Get NLU context from cached context_bundle
            current_question = ""
            current_subagent_id = ""
            allowed_intents = None

            if turn.context_bundle:
                session_data = turn.context_bundle.session or {}
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
                session = self._sessions.get(session_id)
                if session is None:
                    return False
                async with session._lock:
                    if turn.status != TurnStatus.WAITING:
                        return False
                    turn.status = TurnStatus.INVOKED
                    self._cancel_timer_tasks(turn)
                    turn.invocation_task = asyncio.create_task(
                        self._invoke(turn)
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

        session = self._sessions.get(session_id)
        if session is None:
            return

        turn = session.current_turn
        if turn is None:
            return

        async with session._lock:
            if turn.status != TurnStatus.WAITING:
                return
            if not turn.segments:
                return  # No segments accumulated — nothing to invoke

            turn.status = TurnStatus.INVOKED
            self._cancel_timer_tasks(turn)
            turn.invocation_task = asyncio.create_task(
                self._invoke(turn)
            )

            logger.info(
                "turn_assembler.silence_trigger_fired",
                extra={
                    "operation": "turn_assembler.silence_trigger",
                    "status": "success",
                    "session_id": session_id,
                    "turn_id": turn.turn_id,
                    "segment_count": len(turn.segments),
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

        session = self._sessions.get(session_id)
        if session is None:
            return

        turn = session.current_turn
        if turn is None:
            return

        async with session._lock:
            if turn.status != TurnStatus.WAITING:
                return  # Already invoked or cancelled — no-op

            if not turn.segments:
                # Max wait ceiling fired with no segments → ABANDONED (spec)
                turn.status = TurnStatus.ABANDONED
                turn.abort_event.set()
                await turn.event_queue.put(
                    DoneEvent(turn_status="abandoned", turn_id=turn.turn_id)
                )
                logger.info(
                    "turn_assembler.ceiling_abandoned",
                    extra={
                        "operation": "turn_assembler.ceiling_timer",
                        "status": "success",
                        "session_id": session_id,
                        "turn_id": turn.turn_id,
                        "turn_status": "abandoned",
                        "max_wait_ms": max_wait_ms,
                    },
                )
                return

            turn.status = TurnStatus.INVOKED
            self._cancel_timer_tasks(turn)
            turn.invocation_task = asyncio.create_task(
                self._invoke(turn)
            )

            logger.info(
                "turn_assembler.ceiling_trigger_fired",
                extra={
                    "operation": "turn_assembler.ceiling_timer",
                    "status": "success",
                    "session_id": session_id,
                    "turn_id": turn.turn_id,
                    "segment_count": len(turn.segments),
                    "max_wait_ms": max_wait_ms,
                },
            )

    # ------------------------------------------------------------------
    # Invocation path (no HTTP hop — spec requirement)
    # ------------------------------------------------------------------

    async def _invoke(self, turn: Turn) -> None:
        """Assemble segments and call agent_core.stream_turn() with abort signal.

        Per spec: TurnAssembler calls stream_turn() as a Python method — in-process,
        no HTTP, no serialisation. StreamEvents are pushed into the Turn's event_queue.
        The open GET /sessions/{id}/events connection drains the queue via subscribe().

        On abort, cancel() has already enqueued the terminal DoneEvent — this method
        simply exits without enqueuing further events once abort_event is set.

        Args:
            turn: The Turn whose invocation this manages.
        """
        assembled_text = " ".join(s.text.strip() for s in turn.segments)

        turn_input = TurnInput(
            session_id=turn.session_id,
            user_message=assembled_text,
            channel=turn.channel,
            timestamp_ms=turn.started_at_ms,
            user_id=turn.user_id,
        )

        logger.info(
            "turn_assembler.invoke_start",
            extra={
                "operation": "turn_assembler.invoke",
                "status": "success",
                "session_id": turn.session_id,
                "turn_id": turn.turn_id,
                "epoch": turn.epoch,
                "segment_count": len(turn.segments),
                "assembled_length": len(assembled_text),
            },
        )

        start = time.time()
        try:
            async for event in self._agent_core.stream_turn(
                turn_input,
                abort_event=turn.abort_event,
                turn_id=turn.turn_id,
            ):
                if turn.abort_event.is_set():
                    return
                if turn.status != TurnStatus.INVOKED:
                    return
                await turn.event_queue.put(event)
                if isinstance(event, DoneEvent):
                    turn.status = TurnStatus.COMPLETED
                    return
        except asyncio.CancelledError:
            logger.info(
                "turn_assembler.invoke_cancelled",
                extra={
                    "operation": "turn_assembler.invoke",
                    "status": "failure",
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return
        except Exception as e:
            logger.error(
                "turn_assembler.invoke_error",
                extra={
                    "operation": "turn_assembler.invoke",
                    "status": "failure",
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            # Push a DoneEvent so the subscriber doesn't hang
            turn.status = TurnStatus.COMPLETED
            await turn.event_queue.put(
                DoneEvent(
                    turn_status="abandoned",
                    turn_id=turn.turn_id,
                    latency_ms=int((time.time() - start) * 1000),
                )
            )

    # ------------------------------------------------------------------
    # Context fetching
    # ------------------------------------------------------------------

    async def _fetch_context(self, turn: Turn, segment: SegmentInput) -> None:
        """Fetch context_bundle from Memory Layer on first segment.

        Design decision #2: The semantic gate needs current_question and
        current_subagent_id from session state. We fetch this once and cache it
        on the Turn. If the fetch fails, the semantic gate falls through to timers.

        Args:
            turn: The current Turn (cache target).
            segment: The current segment (for user_id).
        """
        turn._context_fetched = True
        try:
            user_id = getattr(segment, "user_id", None) or turn.session_id
            start = time.time()
            bundle = await self._async_memory.context_bundle(
                turn.session_id, user_id
            )
            turn.context_bundle = bundle
            logger.info(
                "turn_assembler.context_fetched",
                extra={
                    "operation": "turn_assembler.fetch_context",
                    "status": "success",
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
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
                    "session_id": turn.session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

    # ------------------------------------------------------------------
    # Timer task helpers
    # ------------------------------------------------------------------

    def _cancel_timer_tasks(self, turn: Turn) -> None:
        """Cancel only timer tasks (silence + ceiling), not the invocation task.

        Args:
            turn: The Turn whose timers to cancel.
        """
        if turn.silence_task and not turn.silence_task.done():
            turn.silence_task.cancel()
            turn.silence_task = None
        if turn.ceiling_task and not turn.ceiling_task.done():
            turn.ceiling_task.cancel()
            turn.ceiling_task = None
