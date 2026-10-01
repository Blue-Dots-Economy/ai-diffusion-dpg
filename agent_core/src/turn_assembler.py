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

2. context_bundle() on first segment: We call async_memory.context_bundle() once on
   the first segment and cache it on the Turn. This is a lightweight read that would
   happen anyway at stream_turn() start — we just pull it earlier.

3. Constructor dependencies: TurnAssembler takes workflow, async_memory, and config
   alongside agent_core.

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
from collections.abc import AsyncGenerator, Callable
from typing import Any, Optional

from src.models import (
    ContextBundle,
    DoneEvent,
    SegmentInput,
    SentenceEvent,
    StreamEvent,
    TurnInput,
)
from src.turn_policy import (
    ON_DISCONNECT_ABORT,
    ON_NEW_INPUT_ABORT_AND_FOLD,
    TurnPolicy,
    resolve_session_idle_ttl_ms,
    resolve_turn_policy,
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

    @abstractmethod
    async def submit(self, session_id: str, segment: SegmentInput) -> "Turn":
        """Start a complete-utterance turn now, interrupting any turn in flight.

        Request-scoped adapter entry (``/stream_turn``). The client has already
        decided the turn is complete, so no trigger policy runs.

        Args:
            session_id: Unique session identifier.
            segment: The complete utterance with its metadata.

        Returns:
            The new, already-invoked Turn. Stream it with :meth:`attach`.

        Raises:
            ValueError: If session_id or the segment text is empty.
        """

    @abstractmethod
    async def attach(self, turn: "Turn") -> AsyncGenerator[StreamEvent, None]:
        """Yield ``turn``'s events until its terminal DoneEvent.

        Args:
            turn: A Turn returned by :meth:`submit`.

        Yields:
            StreamEvent instances in order.
        """
        yield  # pragma: no cover

    @abstractmethod
    def detach(self, turn: "Turn", reason: str) -> None:
        """Tell the assembler a request-scoped consumer went away.

        Synchronous so it can be called from a generator ``finally`` during
        ``GeneratorExit``. Applies the channel's ``on_disconnect`` policy to a
        turn that is still current and in flight; otherwise a no-op.

        Args:
            turn: The Turn whose consumer closed.
            reason: Why (logged), normally ``disconnect``.
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
        workflow: Any = None,
        async_memory: Any = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """Initialise TurnAssembler with injected dependencies.

        Args:
            agent_core: AgentCore instance for calling stream_turn() directly.
            config: Full agent_core config dict. Turn assembler reads defaults from
                    config["reach_layer"]["turn_assembler"] and per-channel overrides
                    from config["reach_layer"]["channels"][<name>]["turn_assembler"].
            workflow: AgentWorkflow instance for intent scoping.
            async_memory: AsyncMemoryLayerBase for fetching context_bundle on first segment.
            clock: Monotonic seconds source; injectable for tests.

        Raises:
            ValueError: If agent_core or config is None.
        """
        if agent_core is None:
            raise ValueError("agent_core must not be None")
        if config is None:
            raise ValueError("config must not be None")

        self._agent_core = agent_core
        self._config = config
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
        self._policies: dict[str, TurnPolicy] = {}
        self._clock: Callable[[], float] = clock or time.monotonic
        self._idle_ttl_s: float = resolve_session_idle_ttl_ms(config) / 1000.0
        self._sweep_interval_s: float = min(self._idle_ttl_s, 60.0)
        self._last_sweep: float = self._clock()

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
            "silence_trigger": dict(self._default_config["silence_trigger"]),
            "max_wait_ceiling": dict(self._default_config["max_wait_ceiling"]),
        }
        # Per-channel overrides: reach_layer.channels.<channel>.turn_assembler
        channel_ta = self._channels_config.get(channel or "", {}).get("turn_assembler", {})
        for section in ("silence_trigger", "max_wait_ceiling"):
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
        caller_agent_id: str | None = None,
    ) -> Session:
        """Look up the Session for session_id, creating it on first access.

        Args:
            session_id: Unique session identifier.
            user_id: User identifier (cached on first access; ignored thereafter).
            channel: Channel name (cached on first access; ignored thereafter).
            caller_agent_id: Optional caller agent identifier.

        Returns:
            The Session — existing or newly created.
        """
        self._evict_idle()
        session = self._sessions.get(session_id)
        if session is None:
            session = Session(
                session_id=session_id,
                user_id=user_id,
                channel=channel or "",
                caller_agent_id=caller_agent_id,
            )
            self._sessions[session_id] = session
        session.last_activity = self._clock()
        return session

    def _evict_idle(self) -> None:
        """Evict idle in-process sessions, at most once per sweep interval.

        A session is evicted only if it has no WAITING/INVOKED turn and no
        subscriber, and was last touched more than ``session_idle_ttl_ms`` ago.
        Memory Layer state is untouched: this is in-process housekeeping for
        request-mode sessions, which never receive ``session_end`` (spec 4.7).
        """
        now = self._clock()
        if now - self._last_sweep < self._sweep_interval_s:
            return
        self._last_sweep = now
        for sid, session in list(self._sessions.items()):
            turn = session.current_turn
            busy = turn is not None and turn.status in (TurnStatus.WAITING, TurnStatus.INVOKED)
            if busy or session.subscribers > 0:
                continue
            if now - session.last_activity <= self._idle_ttl_s:
                continue
            session.ended = True
            session.turn_changed.set()
            self._sessions.pop(sid, None)
            logger.info(
                "turn_assembler.session_evicted",
                extra={"operation": "turn_assembler.evict_idle", "status": "success",
                       "session_id": sid},
            )

    def _policy(self, channel: str | None) -> TurnPolicy:
        """Return the cached streaming-turn policy for ``channel``.

        Args:
            channel: Channel name.

        Returns:
            The resolved TurnPolicy.
        """
        key = channel or ""
        policy = self._policies.get(key)
        if policy is None:
            policy = resolve_turn_policy(self._config, channel)
            self._policies[key] = policy
        return policy

    def _interrupt(self, turn: Turn, reason: str) -> None:
        """Stop ``turn`` cooperatively. Synchronous: safe from a generator ``finally``.

        Marks the turn INTERRUPTED (ABANDONED if it never invoked), sets its
        abort signal, cancels only its timer tasks, and seals its queue with a
        terminal DoneEvent carrying the last stage reached. The invocation task
        is never cancelled: it runs on to the orchestrator's next safe point,
        which records what the turn did (spec §4.4-4.5).

        Args:
            turn: The turn to stop. No-op unless WAITING or INVOKED.
            reason: ``new_input`` | ``disconnect`` | ``cancel`` — logged, and
                ``new_input`` applies the channel's ``on_new_input`` policy.
        """
        if turn.status not in (TurnStatus.WAITING, TurnStatus.INVOKED):
            return
        invoked = turn.status == TurnStatus.INVOKED
        turn.status = TurnStatus.INTERRUPTED if invoked else TurnStatus.ABANDONED
        if reason == "new_input":
            turn.record.write_carryover = (
                self._policy(turn.channel).on_new_input == ON_NEW_INPUT_ABORT_AND_FOLD
            )
        turn.abort_event.set()
        self._cancel_timer_tasks(turn)
        turn.event_queue.put_nowait(DoneEvent(
            turn_status=turn.status.value,
            turn_id=turn.turn_id,
            interrupted_at_stage=turn.record.last_stage or None,
        ))
        logger.info(
            "turn_assembler.interrupt_requested",
            extra={"operation": "turn_assembler.interrupt", "status": "success",
                   "session_id": turn.session_id, "turn_id": turn.turn_id,
                   "reason": reason, "stage": turn.record.last_stage,
                   "turn_status": turn.status.value},
        )

    @staticmethod
    def _draining(turn: Turn) -> bool:
        """Return True while a stopped turn has not finished draining.

        A turn is draining until both its invocation task (reaching a safe
        point) and the ``record.persist_task`` that task's exit created have
        finished. A new turn installed meanwhile must take it as predecessor
        so it waits for, and folds, what the old turn persists (spec §4.4).

        Args:
            turn: The session's current turn, already out of WAITING/INVOKED.

        Returns:
            True if either task exists and is not done.
        """
        return any(
            task is not None and not task.done()
            for task in (turn.invocation_task, turn.record.persist_task)
        )

    async def _await_predecessor(self, pred: Turn, drain_max_ms: int) -> None:
        """Wait for an interrupted predecessor to stop and persist, within budget.

        Waits first for its invocation task (reaching a safe point), then for
        the persist task that task's exit created. Never cancels anything: on
        timeout the caller proceeds, and the predecessor still stops at its next
        safe point and persists late (spec §4.4).

        Args:
            pred: The interrupted turn.
            drain_max_ms: Total budget for both waits.
        """
        start = time.monotonic()
        deadline = start + drain_max_ms / 1000.0

        async def _within(task: "asyncio.Task | None") -> bool:
            if task is None or task.done():
                return True
            remaining = deadline - time.monotonic()
            if remaining > 0:
                await asyncio.wait({task}, timeout=remaining)
            return task.done()

        # The persist task only exists once the invocation task has exited,
        # so it must be read after the first wait, not before.
        reached = await _within(pred.invocation_task) and await _within(pred.record.persist_task)
        drain_ms = int((time.monotonic() - start) * 1000)
        if not reached:
            logger.warning(
                "turn_assembler.drain_timeout",
                extra={"operation": "turn_assembler.await_predecessor",
                       "status": "failure", "session_id": pred.session_id,
                       "turn_id": pred.turn_id, "drain_ms": drain_ms},
            )
            return
        logger.info(
            "turn_assembler.safe_point_reached",
            extra={"operation": "turn_assembler.await_predecessor", "status": "success",
                   "session_id": pred.session_id, "turn_id": pred.turn_id,
                   "stage": pred.record.last_stage, "drain_ms": drain_ms},
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def add_segment(self, session_id: str, segment: SegmentInput) -> None:
        """Accept a text segment and evaluate the policy stack.

        Creates a new Session (and first Turn) if this is the first segment for
        the session. On first segment, also fetches context_bundle from Memory
        Layer and caches it on the Turn.

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
            caller_agent_id=getattr(segment, "caller_agent_id", None),
        )

        async with session._lock:
            turn = session.current_turn

            # Barge-in: new segment arrived while a turn is in flight. Stop it
            # cooperatively; the successor waits for it (spec §4.4) and folds
            # its utterances from Memory Layer (spec §4.6).
            if turn is not None and turn.status == TurnStatus.INVOKED:
                logger.info(
                    "turn_assembler.cancel_and_fold",
                    extra={
                        "operation": "turn_assembler.cancel_and_fold",
                        "status": "success",
                        "session_id": session_id,
                        "cancelled_turn_id": turn.turn_id,
                        "seeded_segment_count": 1,
                        "reason": "new segment arrived while INVOKED — interrupting current turn",
                    },
                )
                self._interrupt(turn, "new_input")
                new_turn = await session.replace_turn(seed_segments=[segment])
                new_turn.predecessor = turn
                turn = new_turn

            elif turn is None or turn.status in (
                TurnStatus.COMPLETED,
                TurnStatus.INTERRUPTED,
                TurnStatus.ABANDONED,
            ):
                # First segment or post-terminal: install a fresh Turn. A turn
                # interrupted earlier (cancel, disconnect) that is still
                # draining is its predecessor, exactly as in submit().
                prev = turn
                turn = await session.replace_turn(seed_segments=[])
                turn.segments.append(segment)
                if prev is not None and self._draining(prev):
                    turn.predecessor = prev
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

        # Outside the lock: cache context on the first segment.
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

        session.subscribers += 1
        try:
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
        finally:
            session.subscribers -= 1

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

        # GH-239: if consent is required and not yet granted, emit the
        # configured ``agent.consent_prompt`` as the session's first
        # utterance instead of staying silent. Bumping ``turn_count`` to 1
        # tells the orchestrator's consent gate to skip the prompt-on-T1
        # branch and run ``verify_consent`` against the user's first
        # transcribed reply. Idempotent via ``consent_prompt_emitted`` so
        # an SSE reconnect mid-consent doesn't double-prompt.
        ask_for_consent: bool = self._config.get("agent", {}).get("ask_for_consent", False)
        if ask_for_consent and bundle.session.get("user_storage_mode") is None:
            if bundle.session.get("consent_prompt_emitted"):
                logger.info(
                    "turn_assembler.consent_prompt_already_emitted",
                    extra={
                        "operation": "turn_assembler._emit_opening_phrase_if_first",
                        "status": "skipped",
                        "reason": "consent_prompt_emitted flag already set",
                        "session_id": session_id,
                    },
                )
                return

            consent_prompt_text: str = (
                self._config.get("agent", {}).get("consent_prompt", "") or ""
            ).strip()
            if not consent_prompt_text:
                # Misconfiguration — preserve previous suppress-and-stay-silent
                # behaviour rather than emitting an empty utterance.
                logger.warning(
                    "turn_assembler.consent_prompt_missing_pending_consent",
                    extra={
                        "operation": "turn_assembler._emit_opening_phrase_if_first",
                        "status": "skipped",
                        "reason": "ask_for_consent=true but agent.consent_prompt is empty",
                        "session_id": session_id,
                    },
                )
                return

            try:
                await self._async_memory.write(
                    session_id, user_id, "session", "consent_prompt_emitted", True
                )
                await self._async_memory.write(
                    session_id, user_id, "session", "turn_count", 1
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "turn_assembler.consent_prompt_flag_write_failed",
                    extra={
                        "operation": "turn_assembler._emit_opening_phrase_if_first",
                        "status": "skipped",
                        "session_id": session_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                return

            async with session._lock:
                op_turn = await session.replace_turn(seed_segments=[])
                op_turn.status = TurnStatus.INVOKED
                await op_turn.event_queue.put(
                    SentenceEvent(text=consent_prompt_text, sentence_index=0)
                )
                await op_turn.event_queue.put(DoneEvent(turn_status="completed"))
                op_turn.status = TurnStatus.COMPLETED

            logger.info(
                "turn_assembler.consent_prompt_emitted",
                extra={
                    "operation": "turn_assembler._emit_opening_phrase_if_first",
                    "status": "emitted",
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

        if not opening_phrase:
            # No phrase to emit — do NOT latch opening_phrase_emitted yet.
            # Latching here would burn the greeting flag for the rest of the
            # session (e.g. a callback that adopts a fallback subagent like
            # ``clarification`` with an empty phrase would silently start
            # and never greet again). The orchestrator's first-turn gate
            # latches the flag on the first user turn, which is the right
            # moment for empty-phrase subagents.
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

    async def submit(self, session_id: str, segment: SegmentInput) -> Turn:
        """Start a complete-utterance turn now, interrupting any turn in flight.

        See :meth:`TurnAssemblerBase.submit`.
        """
        if not session_id:
            raise ValueError("session_id must not be empty")
        if segment is None or not segment.text or not segment.text.strip():
            raise ValueError("segment text must not be empty")
        session = self._get_or_create_session(
            session_id,
            user_id=segment.user_id,
            channel=segment.channel,
            caller_agent_id=segment.caller_agent_id,
        )
        async with session._lock:
            prev = session.current_turn
            predecessor = None
            if prev is not None and prev.status in (TurnStatus.WAITING, TurnStatus.INVOKED):
                was_invoked = prev.status == TurnStatus.INVOKED
                self._interrupt(prev, "new_input")
                predecessor = prev if was_invoked else None
            elif prev is not None and self._draining(prev):
                predecessor = prev  # interrupted earlier, still draining
            turn = await session.replace_turn(seed_segments=[segment])
            turn.predecessor = predecessor
            turn.status = TurnStatus.INVOKED
            turn.invocation_task = asyncio.create_task(self._invoke(turn))
        return turn

    async def attach(self, turn: Turn) -> AsyncGenerator[StreamEvent, None]:
        """Yield ``turn``'s events until its DoneEvent. See :meth:`TurnAssemblerBase.attach`."""
        async for event in turn.iter_events():
            yield event

    def detach(self, turn: Turn, reason: str) -> None:
        """Apply ``on_disconnect`` to a still-current, in-flight turn. See base."""
        if turn is None:
            return
        session = self._sessions.get(turn.session_id)
        if session is None or session.current_turn is not turn:
            return
        if turn.status != TurnStatus.INVOKED:
            return
        if self._policy(turn.channel).on_disconnect == ON_DISCONNECT_ABORT:
            self._interrupt(turn, reason or "disconnect")

    async def cancel(self, session_id: str) -> None:
        """Interrupt the active or waiting turn for this session.

        Idempotent: if the current turn is already terminal, no-op.
        Sets abort_event, cancels timer tasks only, and seals the queue. The
        invocation task is not cancelled: it stops at its next safe point.

        Args:
            session_id: Unique session identifier.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        async with session._lock:
            turn = session.current_turn
            if turn is None:
                return
            self._interrupt(turn, "cancel")

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
            1. Silence trigger — timer that resets on each segment
            2. Max wait ceiling — absolute timer, never resets

        If both timers fire simultaneously, only the first to acquire the lock transitions.

        Args:
            session_id: Session identifier.
            turn: The current Turn.
            config: Resolved per-channel config.
        """
        # Policy 1: Silence trigger — reset on every segment
        silence_config = config.get("silence_trigger", {})
        silence_ms = silence_config.get("silence_ms", 400)

        # Cancel existing silence timer and restart
        if turn.silence_task and not turn.silence_task.done():
            turn.silence_task.cancel()

        turn.silence_task = asyncio.create_task(
            self._silence_timer(session_id, silence_ms)
        )

        # Policy 2: Max wait ceiling — started once, never reset
        ceiling_config = config.get("max_wait_ceiling", {})
        max_wait_ms = ceiling_config.get("max_wait_ms", 8000)

        if turn.ceiling_task is None:
            turn.ceiling_task = asyncio.create_task(
                self._ceiling_timer(session_id, max_wait_ms)
            )

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
        """Wait for any interrupted predecessor, then run stream_turn for this turn.

        Events go to the Turn's queue while the turn is live. Once it is
        interrupted, the remaining events are drained and discarded, so the
        orchestrator reaches its next abort check and persists (spec §4.4-4.5),
        and nothing stale is delivered (#224).

        Args:
            turn: The Turn whose invocation this manages.
        """
        pred, turn.predecessor = turn.predecessor, None
        if pred is not None:
            await self._await_predecessor(pred, self._policy(turn.channel).drain_max_ms)
        # No early return when this turn was itself interrupted while waiting:
        # stream_turn stops at its first abort check and its ``finally``
        # persists this turn's utterance, appended to the carry-over the
        # predecessor just wrote, so the successor still folds it (spec §4.5).

        assembled_text = " ".join(s.text.strip() for s in turn.segments)
        first_segment = turn.segments[0] if turn.segments else None
        turn_input = TurnInput(
            session_id=turn.session_id,
            user_message=assembled_text,
            channel=turn.channel,
            timestamp_ms=turn.started_at_ms,
            user_id=turn.user_id,
            caller_agent_id=turn.caller_agent_id,
            fresh=bool(getattr(first_segment, "fresh", False)) if first_segment else False,
            locale=getattr(first_segment, "locale", None) if first_segment else None,
            metadata=getattr(first_segment, "metadata", None) if first_segment else None,
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
        gen = self._agent_core.stream_turn(
            turn_input,
            abort_event=turn.abort_event,
            turn_id=turn.turn_id,
            record=turn.record,
        )
        try:
            async for event in gen:
                if turn.abort_event.is_set() or turn.status != TurnStatus.INVOKED:
                    continue  # sealed: drain to the next safe point, deliver nothing
                await turn.event_queue.put(event)
                if isinstance(event, DoneEvent):
                    turn.status = TurnStatus.COMPLETED
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
            if turn.status == TurnStatus.INVOKED:
                turn.status = TurnStatus.COMPLETED
                await turn.event_queue.put(
                    DoneEvent(
                        turn_status="abandoned",
                        turn_id=turn.turn_id,
                        latency_ms=int((time.time() - start) * 1000),
                    )
                )
        finally:
            await gen.aclose()

    # ------------------------------------------------------------------
    # Context fetching
    # ------------------------------------------------------------------

    async def _fetch_context(self, turn: Turn, segment: SegmentInput) -> None:
        """Fetch context_bundle from Memory Layer on first segment.

        Design decision #2: current_question and current_subagent_id come from
        session state. We fetch this once and cache it on the Turn. If the fetch
        fails the turn proceeds without cached context; timers still function.

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
            # Context fetch failure is non-fatal: the turn proceeds without
            # cached context. Timers still function.
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
