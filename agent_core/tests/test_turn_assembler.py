"""
Tests for TurnAssembler (#72): core classes, policy stack, buffer management.

Covers:
  - TurnStatus state machine
  - Session and Turn lifecycle
  - TurnAssemblerBase ABC enforcement
  - TurnAssembler: add_segment, subscribe, cancel, session_end
  - Policy stack: silence trigger, max wait ceiling, semantic gate
  - Invocation path: stream_turn() called directly with abort_event/turn_id
  - Memory consistency on cancellation (#83)
  - Edge cases: empty text, missing session, concurrent timers
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import (
    ContextBundle,
    DoneEvent,
    NLUResult,
    SegmentInput,
    SentenceEvent,
    SignalEvent,
)
from src.turn_assembler import (
    TurnAssembler,
    TurnAssemblerBase,
    TurnStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(
    semantic_enabled=False,
    confidence_threshold=0.75,
    silence_ms=50,       # Short for fast tests
    max_wait_ms=200,     # Short for fast tests
    channel_overrides=None,
):
    """Build a config dict for tests.

    channel_overrides uses the flat format for convenience:
      {"voice": {"silence_trigger": {"silence_ms": 200}}}
    and is placed under top-level channels.<name>.turn_assembler (post-GH-137).
    """
    cfg = {
        "reach_layer": {
            "turn_assembler": {
                "semantic_gate": {
                    "enabled": semantic_enabled,
                    "confidence_threshold": confidence_threshold,
                },
                "silence_trigger": {"silence_ms": silence_ms},
                "max_wait_ceiling": {"max_wait_ms": max_wait_ms},
            },
        },
        "channels": {},
    }
    if channel_overrides:
        for ch, overrides in channel_overrides.items():
            cfg["channels"][ch] = {"turn_assembler": overrides}
    return cfg


def _make_segment(text="hello", channel="cli", user_id="u1", timestamp_ms=1000):
    return SegmentInput(text=text, channel=channel, user_id=user_id, timestamp_ms=timestamp_ms)


def _make_mock_agent_core():
    """Create a mock AgentCore whose stream_turn yields a simple event sequence."""
    agent = MagicMock()

    async def _stream(turn_input, *, abort_event=None, turn_id=""):
        yield SignalEvent(stage="memory_read", status="start")
        yield SentenceEvent(text="Hello!", sentence_index=0)
        yield DoneEvent(turn_id=turn_id or "t-1", turn_status="completed")

    agent.stream_turn = _stream
    return agent


def _make_assembler(
    agent_core=None,
    config=None,
    nlu_processor=None,
    llm_wrapper=None,
    workflow=None,
    async_memory=None,
):
    return TurnAssembler(
        agent_core=agent_core or _make_mock_agent_core(),
        config=config or _make_config(),
        nlu_processor=nlu_processor,
        llm_wrapper=llm_wrapper,
        workflow=workflow,
        async_memory=async_memory,
    )


# ---------------------------------------------------------------------------
# TurnStatus
# ---------------------------------------------------------------------------


class TestTurnStatus:

    def test_status_values(self):
        assert TurnStatus.WAITING == "waiting"
        assert TurnStatus.INVOKED == "invoked"
        assert TurnStatus.COMPLETED == "completed"
        assert TurnStatus.INTERRUPTED == "interrupted"
        assert TurnStatus.ABANDONED == "abandoned"

    def test_status_is_string(self):
        assert isinstance(TurnStatus.WAITING, str)


# ---------------------------------------------------------------------------
# Session and Turn lifecycle defaults
# ---------------------------------------------------------------------------


class TestSessionAndTurnDefaults:

    @pytest.mark.asyncio
    async def test_session_default_creation(self):
        from src.session import Session as SessionClass
        session = SessionClass(session_id="s1", user_id=None, channel="cli")
        assert session.session_id == "s1"
        assert session.current_turn is None
        assert session.ended is False

    @pytest.mark.asyncio
    async def test_turn_default_creation(self):
        from src.session import Session as SessionClass
        session = SessionClass(session_id="s1", user_id=None, channel="cli")
        turn = await session.replace_turn(seed_segments=[])
        assert turn.status == TurnStatus.WAITING
        assert turn.segments == []
        assert turn.silence_task is None
        assert turn.ceiling_task is None
        assert turn.invocation_task is None
        assert turn.context_bundle is None
        assert turn._context_fetched is False

    @pytest.mark.asyncio
    async def test_turn_started_at_ms_is_set(self):
        from src.session import Session as SessionClass
        session = SessionClass(session_id="s1", user_id=None, channel="cli")
        turn = await session.replace_turn(seed_segments=[])
        assert turn.started_at_ms > 0


# ---------------------------------------------------------------------------
# TurnAssemblerBase ABC
# ---------------------------------------------------------------------------


class TestTurnAssemblerBaseABC:

    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            TurnAssemblerBase()


# ---------------------------------------------------------------------------
# TurnAssembler construction
# ---------------------------------------------------------------------------


class TestTurnAssemblerConstruction:

    def test_requires_agent_core(self):
        with pytest.raises(ValueError, match="agent_core"):
            TurnAssembler(agent_core=None, config={})

    def test_requires_config(self):
        with pytest.raises(ValueError, match="config"):
            TurnAssembler(agent_core=MagicMock(), config=None)

    def test_default_config_values(self):
        ta = _make_assembler(config={"reach_layer": {"turn_assembler": {}}})
        # Should use defaults without crashing
        assert ta._default_config["semantic_gate"]["enabled"] is False
        assert ta._default_config["silence_trigger"]["silence_ms"] == 400
        assert ta._default_config["max_wait_ceiling"]["max_wait_ms"] == 8000

    def test_config_override(self):
        ta = _make_assembler(config=_make_config(silence_ms=999))
        assert ta._default_config["silence_trigger"]["silence_ms"] == 999


# ---------------------------------------------------------------------------
# Config resolution with channel overrides
# ---------------------------------------------------------------------------


class TestConfigResolution:

    def test_default_config_used_when_no_override(self):
        ta = _make_assembler(config=_make_config(silence_ms=400))
        resolved = ta._resolve_config("unknown_channel")
        assert resolved["silence_trigger"]["silence_ms"] == 400

    def test_channel_override_applied(self):
        ta = _make_assembler(config=_make_config(
            silence_ms=400,
            channel_overrides={
                "voice": {"silence_trigger": {"silence_ms": 200}},
            },
        ))
        resolved = ta._resolve_config("voice")
        assert resolved["silence_trigger"]["silence_ms"] == 200

    def test_channel_override_partial(self):
        """Override only one section — others keep defaults."""
        ta = _make_assembler(config=_make_config(
            silence_ms=400,
            max_wait_ms=8000,
            channel_overrides={
                "web": {"max_wait_ceiling": {"max_wait_ms": 15000}},
            },
        ))
        resolved = ta._resolve_config("web")
        assert resolved["silence_trigger"]["silence_ms"] == 400  # Default
        assert resolved["max_wait_ceiling"]["max_wait_ms"] == 15000  # Override


# ---------------------------------------------------------------------------
# add_segment
# ---------------------------------------------------------------------------


class TestAddSegment:

    @pytest.mark.asyncio
    async def test_empty_session_id_ignored(self):
        ta = _make_assembler()
        await ta.add_segment("", _make_segment())
        assert len(ta._sessions) == 0

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self):
        ta = _make_assembler()
        await ta.add_segment("s1", SegmentInput(text=""))
        assert len(ta._sessions) == 0

    @pytest.mark.asyncio
    async def test_whitespace_text_ignored(self):
        ta = _make_assembler()
        await ta.add_segment("s1", SegmentInput(text="   "))
        assert len(ta._sessions) == 0

    @pytest.mark.asyncio
    async def test_none_segment_ignored(self):
        ta = _make_assembler()
        await ta.add_segment("s1", None)
        assert len(ta._sessions) == 0

    @pytest.mark.asyncio
    async def test_creates_buffer_on_first_segment(self):
        ta = _make_assembler()
        await ta.add_segment("s1", _make_segment("hello"))
        assert "s1" in ta._sessions
        session = ta._sessions["s1"]
        assert session.current_turn is not None
        assert any(s.text.strip() == "hello" for s in session.current_turn.segments)

    @pytest.mark.asyncio
    async def test_appends_to_existing_buffer(self):
        ta = _make_assembler()
        await ta.add_segment("s1", _make_segment("hello"))
        await ta.add_segment("s1", _make_segment("world"))
        session = ta._sessions["s1"]
        texts = [s.text.strip() for s in session.current_turn.segments]
        assert texts == ["hello", "world"]

    @pytest.mark.asyncio
    async def test_caches_metadata_from_first_segment(self):
        ta = _make_assembler()
        seg = _make_segment("hi", channel="voice", user_id="u42", timestamp_ms=5000)
        await ta.add_segment("s1", seg)
        session = ta._sessions["s1"]
        assert session.channel == "voice"
        assert session.user_id == "u42"
        # started_at_ms is set at turn creation — check it's non-zero
        assert session.current_turn.started_at_ms > 0

    @pytest.mark.asyncio
    async def test_segment_triggers_barge_in_when_invoked(self):
        """New segment while INVOKED cancels current turn and starts a new one."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hello"))
        session = ta._sessions["s1"]
        first_turn = session.current_turn
        # Simulate turn in flight
        first_turn.status = TurnStatus.INVOKED
        first_turn.invocation_task = asyncio.create_task(asyncio.sleep(10))

        await ta.add_segment("s1", _make_segment("new message"))

        # Original turn cancelled, new turn installed
        assert first_turn.status == TurnStatus.INTERRUPTED
        assert first_turn.abort_event.is_set()
        # New turn has the barge-in segment
        new_turn = session.current_turn
        assert new_turn is not first_turn
        assert any(s.text.strip() == "new message" for s in new_turn.segments)

    @pytest.mark.asyncio
    async def test_cancel_and_fold_log_has_required_structured_fields(self, caplog):
        """When a new segment arrives during INVOKED, the cancel-and-fold log
        entry uses operation=turn_assembler.cancel_and_fold and carries
        cancelled_turn_id + folded_segment_count fields per #200."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("first"))
        session = ta._sessions["s1"]
        first_turn = session.current_turn
        cancelled_turn_id = first_turn.turn_id
        # Simulate the prior turn being in flight.
        first_turn.status = TurnStatus.INVOKED
        first_turn.invocation_task = asyncio.create_task(asyncio.sleep(10))

        caplog.clear()
        with caplog.at_level(logging.INFO):
            await ta.add_segment("s1", _make_segment("second"))

        # Find the cancel-and-fold log record.
        fold_records = [
            r for r in caplog.records
            if getattr(r, "operation", None) == "turn_assembler.cancel_and_fold"
        ]
        assert len(fold_records) == 1, (
            f"expected exactly one cancel_and_fold log record; "
            f"got {len(fold_records)}"
        )
        rec = fold_records[0]
        assert rec.msg == "turn_assembler.cancel_and_fold"
        assert rec.status == "success"
        assert rec.session_id == "s1"
        assert rec.cancelled_turn_id == cancelled_turn_id
        # Folded segment count: only the triggering segment seeds today.
        assert rec.folded_segment_count == 1

    @pytest.mark.asyncio
    async def test_segment_ignored_when_completed(self):
        """Segment arriving when turn is COMPLETED installs a fresh Turn."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hello"))
        session = ta._sessions["s1"]
        first_turn = session.current_turn
        first_turn.status = TurnStatus.COMPLETED
        # A new segment after COMPLETED should create a new turn, not be ignored
        await ta.add_segment("s1", _make_segment("next"))
        assert session.current_turn is not first_turn

    @pytest.mark.asyncio
    async def test_strips_whitespace_from_text(self):
        ta = _make_assembler()
        await ta.add_segment("s1", _make_segment("  hello  "))
        session = ta._sessions["s1"]
        # The segment text is preserved as-is in SegmentInput; stripping happens
        # when assembling for stream_turn (joined via .strip()). The raw segment
        # is stored in Turn.segments.
        assert any("hello" in s.text for s in session.current_turn.segments)


# ---------------------------------------------------------------------------
# Silence trigger
# ---------------------------------------------------------------------------


class TestSilenceTrigger:

    @pytest.mark.asyncio
    async def test_silence_timer_triggers_invocation(self):
        """After silence_ms elapses with no new segment, stream_turn is called."""
        agent = _make_mock_agent_core()
        ta = _make_assembler(agent_core=agent, config=_make_config(silence_ms=30))

        await ta.add_segment("s1", _make_segment("hello"))
        # Wait for silence timer to fire
        await asyncio.sleep(0.1)

        session = ta._sessions.get("s1")
        # Turn should have been invoked (status may be COMPLETED by now)
        assert session is not None
        assert session.current_turn is not None
        assert session.current_turn.status in (TurnStatus.INVOKED, TurnStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_silence_timer_resets_on_new_segment(self):
        """Adding a new segment resets the silence timer."""
        ta = _make_assembler(config=_make_config(silence_ms=80, max_wait_ms=5000))

        await ta.add_segment("s1", _make_segment("hello"))
        await asyncio.sleep(0.04)  # 40ms — timer not yet fired
        await ta.add_segment("s1", _make_segment("world"))
        await asyncio.sleep(0.04)  # another 40ms — timer reset, still hasn't fired

        session = ta._sessions["s1"]
        assert session.current_turn.status == TurnStatus.WAITING
        assert len(session.current_turn.segments) == 2


# ---------------------------------------------------------------------------
# Max wait ceiling
# ---------------------------------------------------------------------------


class TestMaxWaitCeiling:

    @pytest.mark.asyncio
    async def test_ceiling_triggers_after_max_wait(self):
        """Ceiling timer fires after max_wait_ms regardless of silence timer."""
        agent = _make_mock_agent_core()
        ta = _make_assembler(
            agent_core=agent,
            config=_make_config(silence_ms=5000, max_wait_ms=50),
        )

        await ta.add_segment("s1", _make_segment("hello"))
        await asyncio.sleep(0.15)

        session = ta._sessions.get("s1")
        assert session is not None
        assert session.current_turn is not None
        assert session.current_turn.status in (TurnStatus.INVOKED, TurnStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_ceiling_with_no_segments_abandons(self):
        """Ceiling timer firing with no segments transitions turn to ABANDONED."""
        from src.session import Session as SessionClass
        ta = _make_assembler(config=_make_config(max_wait_ms=30))

        # Create a Session with a Turn directly but don't add segments
        session = SessionClass(session_id="s1", user_id=None, channel="cli")
        ta._sessions["s1"] = session
        # Install a turn with no segments
        import asyncio as _asyncio
        turn = await session.replace_turn(seed_segments=[])
        turn.ceiling_task = _asyncio.create_task(ta._ceiling_timer("s1", 30))

        await asyncio.sleep(0.1)

        assert turn.status == TurnStatus.ABANDONED

    @pytest.mark.asyncio
    async def test_ceiling_never_resets(self):
        """Ceiling timer is started once and never reset by new segments."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=100))

        await ta.add_segment("s1", _make_segment("hello"))
        session = ta._sessions["s1"]
        ceiling_task = session.current_turn.ceiling_task

        await ta.add_segment("s1", _make_segment("world"))
        # Ceiling task should be the same object — not recreated
        assert session.current_turn.ceiling_task is ceiling_task


# ---------------------------------------------------------------------------
# Semantic gate
# ---------------------------------------------------------------------------


class TestSemanticGate:

    @pytest.mark.asyncio
    async def test_gate_triggers_on_high_confidence(self):
        """When NLU confidence >= threshold, invocation triggers immediately."""
        nlu = MagicMock()
        nlu.process.return_value = NLUResult(
            intent="greeting", confidence=0.9, entities={}, sentiment="positive"
        )

        agent = _make_mock_agent_core()
        ta = _make_assembler(
            agent_core=agent,
            config=_make_config(semantic_enabled=True, confidence_threshold=0.75, silence_ms=5000),
            nlu_processor=nlu,
            llm_wrapper=MagicMock(),
        )

        await ta.add_segment("s1", _make_segment("hello"))
        await asyncio.sleep(0.1)

        session = ta._sessions.get("s1")
        assert session is not None
        assert session.current_turn is not None
        assert session.current_turn.status in (TurnStatus.INVOKED, TurnStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_gate_falls_through_on_low_confidence(self):
        """When NLU confidence < threshold, falls through to silence timer."""
        nlu = MagicMock()
        nlu.process.return_value = NLUResult(
            intent="unknown", confidence=0.3, entities={}, sentiment="neutral"
        )

        ta = _make_assembler(
            config=_make_config(semantic_enabled=True, confidence_threshold=0.75, silence_ms=5000, max_wait_ms=5000),
            nlu_processor=nlu,
            llm_wrapper=MagicMock(),
        )

        await ta.add_segment("s1", _make_segment("hello"))
        # Should NOT trigger immediately — falls through to timers
        session = ta._sessions["s1"]
        assert session.current_turn.status == TurnStatus.WAITING

    @pytest.mark.asyncio
    async def test_gate_falls_through_on_unknown_intent(self):
        """High confidence but 'unknown' intent does not trigger."""
        nlu = MagicMock()
        nlu.process.return_value = NLUResult(
            intent="unknown", confidence=0.95, entities={}, sentiment="neutral"
        )

        ta = _make_assembler(
            config=_make_config(semantic_enabled=True, silence_ms=5000, max_wait_ms=5000),
            nlu_processor=nlu,
            llm_wrapper=MagicMock(),
        )

        await ta.add_segment("s1", _make_segment("hello"))
        session = ta._sessions["s1"]
        assert session.current_turn.status == TurnStatus.WAITING

    @pytest.mark.asyncio
    async def test_gate_graceful_on_nlu_error(self):
        """NLU exception → log and fall through, never block."""
        nlu = MagicMock()
        nlu.process.side_effect = RuntimeError("NLU down")

        ta = _make_assembler(
            config=_make_config(semantic_enabled=True, silence_ms=5000, max_wait_ms=5000),
            nlu_processor=nlu,
            llm_wrapper=MagicMock(),
        )

        await ta.add_segment("s1", _make_segment("hello"))
        session = ta._sessions["s1"]
        assert session.current_turn.status == TurnStatus.WAITING  # Fell through

    @pytest.mark.asyncio
    async def test_gate_disabled_skips_nlu(self):
        """When semantic gate is disabled, NLU is never called."""
        nlu = MagicMock()

        ta = _make_assembler(
            config=_make_config(semantic_enabled=False, silence_ms=5000, max_wait_ms=5000),
            nlu_processor=nlu,
            llm_wrapper=MagicMock(),
        )

        await ta.add_segment("s1", _make_segment("hello"))
        nlu.process.assert_not_called()

    @pytest.mark.asyncio
    async def test_gate_uses_context_bundle(self):
        """Semantic gate uses cached context_bundle for NLU context."""
        nlu = MagicMock()
        nlu.process.return_value = NLUResult(
            intent="greeting", confidence=0.9, entities={}, sentiment="positive"
        )

        async_memory = AsyncMock()
        async_memory.context_bundle.return_value = ContextBundle(
            session={"current_question": "What trade?", "current_subagent_id": "profile_building"},
            profile={},
        )

        workflow = MagicMock()
        workflow.start_subagent_id = "profile_building"
        workflow.subagents = {"profile_building": MagicMock(valid_intents=["greeting"], special_handler=None)}
        workflow.global_intents = ["termination_intent"]

        agent = _make_mock_agent_core()
        ta = _make_assembler(
            agent_core=agent,
            config=_make_config(semantic_enabled=True, silence_ms=5000),
            nlu_processor=nlu,
            llm_wrapper=MagicMock(),
            workflow=workflow,
            async_memory=async_memory,
        )

        await ta.add_segment("s1", _make_segment("hello"))
        await asyncio.sleep(0.1)

        # NLU should have been called with context
        call_args = nlu.process.call_args
        assert call_args.kwargs.get("current_question") == "What trade?"
        assert call_args.kwargs.get("current_subagent_id") == "profile_building"


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------


class TestSubscribe:

    @pytest.mark.asyncio
    async def test_subscribe_yields_events(self):
        """subscribe() yields events from the invocation pipeline.

        subscribe() has a while True loop for multi-turn SSE — we run it in a
        background task and cancel after collecting the DoneEvent.
        """
        agent = _make_mock_agent_core()
        ta = _make_assembler(agent_core=agent, config=_make_config(silence_ms=30))

        await ta.add_segment("s1", _make_segment("hello"))

        events = []
        async def collect():
            async for event in ta.subscribe("s1"):
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await asyncio.sleep(0.3)
        collect_task.cancel()
        try:
            await collect_task
        except asyncio.CancelledError:
            pass

        assert len(events) >= 1
        assert isinstance(events[-1], DoneEvent)

    @pytest.mark.asyncio
    async def test_subscribe_creates_session_if_needed(self):
        """subscribe() creates a Session if one doesn't exist."""
        from src.session import Session as SessionClass
        ta = _make_assembler()

        # Put events manually on a pre-created turn to simulate invocation
        session = ta._get_or_create_session("s1")
        turn = await session.replace_turn(seed_segments=[])
        turn.status = TurnStatus.INVOKED
        await turn.event_queue.put(DoneEvent(turn_status="completed"))
        turn.status = TurnStatus.COMPLETED

        events = []
        async def collect():
            async for event in ta.subscribe("s1"):
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await asyncio.sleep(0.2)
        collect_task.cancel()
        try:
            await collect_task
        except asyncio.CancelledError:
            pass

        assert len(events) == 1
        assert isinstance(events[0], DoneEvent)

    @pytest.mark.asyncio
    async def test_subscribe_waits_for_next_turn_after_done(self):
        """After DoneEvent, subscribe() waits for turn_changed instead of blocking.

        subscribe() rolls over to the next Turn when turn_changed fires. We
        cancel the task after the first Done to verify session stays alive.
        """
        ta = _make_assembler()

        session = ta._get_or_create_session("s1")
        turn = await session.replace_turn(seed_segments=[])
        turn.status = TurnStatus.INVOKED
        await turn.event_queue.put(DoneEvent(turn_status="completed"))
        turn.status = TurnStatus.COMPLETED

        async def drain():
            async for _ in ta.subscribe("s1"):
                pass

        drain_task = asyncio.create_task(drain())
        await asyncio.sleep(0.1)  # Allow DoneEvent to be processed
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass

        # Session should still exist (not ended)
        assert "s1" in ta._sessions
        assert ta._sessions["s1"].current_turn is turn  # still points to first turn

    @pytest.mark.asyncio
    async def test_subscribe_empty_session_returns(self):
        """subscribe() with empty session_id returns immediately."""
        ta = _make_assembler()
        events = []
        async for event in ta.subscribe(""):
            events.append(event)
        assert events == []


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


class TestCancel:

    @pytest.mark.asyncio
    async def test_cancel_waiting_transitions_to_abandoned(self):
        ta = _make_assembler()
        await ta.add_segment("s1", _make_segment("hello"))
        session = ta._sessions["s1"]
        turn = session.current_turn
        # Cancel while silence timer is still waiting
        if turn.silence_task and not turn.silence_task.done():
            turn.silence_task.cancel()

        await ta.cancel("s1")

        assert turn.status == TurnStatus.ABANDONED

    @pytest.mark.asyncio
    async def test_cancel_waiting_pushes_done_event(self):
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hello"))
        session = ta._sessions["s1"]
        turn = session.current_turn

        await ta.cancel("s1")

        event = await turn.event_queue.get()
        assert isinstance(event, DoneEvent)
        assert event.turn_status == "abandoned"

    @pytest.mark.asyncio
    async def test_cancel_invoked_transitions_to_interrupted(self):
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hello"))
        session = ta._sessions["s1"]
        turn = session.current_turn
        turn.status = TurnStatus.INVOKED
        turn.invocation_task = asyncio.create_task(asyncio.sleep(10))

        await ta.cancel("s1")

        assert turn.status == TurnStatus.INTERRUPTED
        event = await turn.event_queue.get()
        assert isinstance(event, DoneEvent)
        assert event.turn_status == "interrupted"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_session_noop(self):
        ta = _make_assembler()
        await ta.cancel("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_cancel_completed_noop(self):
        from src.session import Session as SessionClass
        ta = _make_assembler()
        session = ta._get_or_create_session("s1")
        turn = await session.replace_turn(seed_segments=[])
        turn.status = TurnStatus.COMPLETED

        await ta.cancel("s1")
        assert turn.status == TurnStatus.COMPLETED  # Unchanged


# ---------------------------------------------------------------------------
# session_end
# ---------------------------------------------------------------------------


class TestSessionEnd:

    @pytest.mark.asyncio
    async def test_session_end_removes_session(self):
        ta = _make_assembler()
        session = ta._get_or_create_session("s1")
        await session.replace_turn(seed_segments=[])

        await ta.session_end("s1")
        assert "s1" not in ta._sessions

    @pytest.mark.asyncio
    async def test_session_end_nonexistent_noop(self):
        ta = _make_assembler()
        await ta.session_end("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_session_end_cancels_tasks(self):
        ta = _make_assembler()
        session = ta._get_or_create_session("s1")
        turn = await session.replace_turn(seed_segments=[])
        silence = asyncio.create_task(asyncio.sleep(10))
        ceiling = asyncio.create_task(asyncio.sleep(10))
        invocation = asyncio.create_task(asyncio.sleep(10))
        turn.silence_task = silence
        turn.ceiling_task = ceiling
        turn.invocation_task = invocation

        await ta.session_end("s1")

        # Allow event loop to process cancellations
        await asyncio.sleep(0)

        assert silence.cancelled()
        assert ceiling.cancelled()
        assert invocation.cancelled()


# ---------------------------------------------------------------------------
# Invocation path
# ---------------------------------------------------------------------------


class TestInvocation:

    @pytest.mark.asyncio
    async def test_invoke_calls_stream_turn(self):
        """_invoke() calls agent_core.stream_turn() directly."""
        agent = _make_mock_agent_core()
        ta = _make_assembler(agent_core=agent, config=_make_config(silence_ms=30))

        await ta.add_segment("s1", _make_segment("hello world"))
        await asyncio.sleep(0.15)

        session = ta._sessions.get("s1")
        assert session is not None
        assert session.current_turn.status == TurnStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_invoke_assembles_text(self):
        """Multiple segments are joined with spaces."""
        captured_inputs = []

        async def capture_stream(turn_input, *, abort_event=None, turn_id=""):
            captured_inputs.append(turn_input)
            yield DoneEvent(turn_status="completed", turn_id=turn_id)

        agent = MagicMock()
        agent.stream_turn = capture_stream

        ta = _make_assembler(agent_core=agent, config=_make_config(silence_ms=50, max_wait_ms=5000))

        await ta.add_segment("s1", _make_segment("मुझे"))
        await ta.add_segment("s1", _make_segment("जॉब चाहिए"))

        await asyncio.sleep(0.2)

        assert len(captured_inputs) == 1
        assert captured_inputs[0].user_message == "मुझे जॉब चाहिए"

    @pytest.mark.asyncio
    async def test_invoke_pushes_done_event_on_error(self):
        """On stream_turn() error, a DoneEvent(abandoned) is pushed to queue."""
        async def failing_stream(turn_input, *, abort_event=None, turn_id=""):
            raise RuntimeError("boom")
            yield  # Make it a generator

        agent = MagicMock()
        agent.stream_turn = failing_stream

        ta = _make_assembler(agent_core=agent, config=_make_config(silence_ms=30))

        await ta.add_segment("s1", _make_segment("hello"))
        await asyncio.sleep(0.15)

        session = ta._sessions.get("s1")
        assert session is not None
        turn = session.current_turn
        event = await turn.event_queue.get()
        assert isinstance(event, DoneEvent)
        assert event.turn_status == "abandoned"

    @pytest.mark.asyncio
    async def test_invoke_uses_first_segment_metadata(self):
        """TurnInput is constructed with session channel/user_id and turn's started_at_ms."""
        captured = []

        async def capture_stream(turn_input, *, abort_event=None, turn_id=""):
            captured.append(turn_input)
            yield DoneEvent(turn_status="completed", turn_id=turn_id)

        agent = MagicMock()
        agent.stream_turn = capture_stream

        ta = _make_assembler(agent_core=agent, config=_make_config(silence_ms=30))

        seg = _make_segment("hello", channel="voice", user_id="u99", timestamp_ms=42000)
        await ta.add_segment("s1", seg)
        await asyncio.sleep(0.15)

        assert len(captured) == 1
        ti = captured[0]
        assert ti.channel == "voice"
        assert ti.user_id == "u99"
        # timestamp_ms comes from turn.started_at_ms (set at turn creation), not first segment
        assert ti.timestamp_ms > 0


# ---------------------------------------------------------------------------
# Context fetch
# ---------------------------------------------------------------------------


class TestContextFetch:

    @pytest.mark.asyncio
    async def test_context_fetched_on_first_segment(self):
        async_memory = AsyncMock()
        async_memory.context_bundle.return_value = ContextBundle(
            session={"current_question": "What trade?"}, profile={},
        )

        ta = _make_assembler(
            config=_make_config(silence_ms=5000, max_wait_ms=5000),
            async_memory=async_memory,
        )

        await ta.add_segment("s1", _make_segment("hello"))

        async_memory.context_bundle.assert_called_once()
        assert ta._sessions["s1"].current_turn.context_bundle is not None

    @pytest.mark.asyncio
    async def test_context_not_refetched_on_second_segment(self):
        async_memory = AsyncMock()
        async_memory.context_bundle.return_value = ContextBundle.empty()

        ta = _make_assembler(
            config=_make_config(silence_ms=5000, max_wait_ms=5000),
            async_memory=async_memory,
        )

        await ta.add_segment("s1", _make_segment("hello"))
        await ta.add_segment("s1", _make_segment("world"))

        assert async_memory.context_bundle.call_count == 1

    @pytest.mark.asyncio
    async def test_context_fetch_error_non_fatal(self):
        async_memory = AsyncMock()
        async_memory.context_bundle.side_effect = RuntimeError("Memory down")

        ta = _make_assembler(
            config=_make_config(silence_ms=5000, max_wait_ms=5000),
            async_memory=async_memory,
        )

        await ta.add_segment("s1", _make_segment("hello"))

        turn = ta._sessions["s1"].current_turn
        assert turn._context_fetched is True
        assert turn.context_bundle is None  # Failed gracefully
        assert turn.status == TurnStatus.WAITING  # Still operational

    @pytest.mark.asyncio
    async def test_no_async_memory_skips_fetch(self):
        ta = _make_assembler(
            config=_make_config(silence_ms=5000, max_wait_ms=5000),
            async_memory=None,
        )

        await ta.add_segment("s1", _make_segment("hello"))
        assert ta._sessions["s1"].current_turn._context_fetched is False


# ---------------------------------------------------------------------------
# Buffer management
# ---------------------------------------------------------------------------


class TestBufferManagement:

    @pytest.mark.asyncio
    async def test_session_identity_preserved_across_turns(self):
        """Session preserves channel/user_id across turn rollovers."""
        from src.session import Session as SessionClass
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hello", channel="voice", user_id="u1"))
        session = ta._sessions["s1"]

        assert isinstance(session, SessionClass)
        assert session.session_id == "s1"
        assert session.channel == "voice"
        assert session.user_id == "u1"

        # After terminal turn, a new turn is installed; session identity is preserved.
        first_turn = session.current_turn
        first_turn.status = TurnStatus.COMPLETED
        await ta.add_segment("s1", _make_segment("world", channel="voice", user_id="u1"))

        assert session.channel == "voice"
        assert session.user_id == "u1"
        assert session.current_turn is not first_turn

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self):
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))

        await ta.add_segment("s1", _make_segment("hello"))
        await ta.add_segment("s2", _make_segment("world"))

        assert "s1" in ta._sessions
        assert "s2" in ta._sessions
        s1_texts = [s.text.strip() for s in ta._sessions["s1"].current_turn.segments]
        s2_texts = [s.text.strip() for s in ta._sessions["s2"].current_turn.segments]
        assert s1_texts == ["hello"]
        assert s2_texts == ["world"]


# ---------------------------------------------------------------------------
# Concurrent timer race
# ---------------------------------------------------------------------------


class TestConcurrentTimerRace:

    @pytest.mark.asyncio
    async def test_only_first_timer_acquires_lock(self):
        """If silence and ceiling fire together, only one transitions state."""
        agent = _make_mock_agent_core()
        ta = _make_assembler(
            agent_core=agent,
            config=_make_config(silence_ms=30, max_wait_ms=30),
        )

        await ta.add_segment("s1", _make_segment("hello"))
        await asyncio.sleep(0.2)

        session = ta._sessions.get("s1")
        assert session is not None
        # Should be INVOKED or COMPLETED, not stuck
        assert session.current_turn.status in (TurnStatus.INVOKED, TurnStatus.COMPLETED)


# ---------------------------------------------------------------------------
# End-to-end: segment → subscribe → events
# ---------------------------------------------------------------------------


class TestEndToEnd:

    @pytest.mark.asyncio
    async def test_segment_to_events_flow(self):
        """Full flow: add segment → silence triggers → subscribe yields events.

        subscribe() rolls over turns — cancel the task once the DoneEvent is received.
        """
        agent = _make_mock_agent_core()
        ta = _make_assembler(agent_core=agent, config=_make_config(silence_ms=30))

        events = []
        async def collect():
            async for event in ta.subscribe("s1"):
                events.append(event)

        collect_task = asyncio.create_task(collect())

        # Add segment — silence timer will trigger invocation
        await ta.add_segment("s1", _make_segment("hello"))

        # Wait for events then cancel
        await asyncio.sleep(0.3)
        collect_task.cancel()
        try:
            await collect_task
        except asyncio.CancelledError:
            pass

        assert len(events) >= 1
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].turn_status == "completed"

    @pytest.mark.asyncio
    async def test_barge_in_new_turn_uses_only_correction(self):
        """After barge-in, the new turn processes ONLY the barge-in utterance.

        GH-152 Phase 2: the original interrupted turn is aborted. Scenario:
        user says "मुझे जॉब चाहिए" (turn starts → LLM begins responding), then
        barges in with "wait wait that is not correct". The new Turn installs
        with ONLY the correction as its segment.
        """
        captured_inputs = []

        call_count = 0

        async def slow_then_capture(turn_input, *, abort_event=None, turn_id=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First invocation: slow enough to be interrupted
                for _ in range(100):
                    if abort_event is not None and abort_event.is_set():
                        return
                    await asyncio.sleep(0.02)
                yield DoneEvent(turn_status="completed", turn_id=turn_id)
            else:
                # Second invocation: capture assembled_text and complete
                captured_inputs.append(turn_input.user_message)
                yield DoneEvent(turn_status="completed", turn_id=turn_id)

        agent = MagicMock()
        agent.stream_turn = slow_then_capture

        ta = _make_assembler(
            agent_core=agent,
            config=_make_config(silence_ms=30, max_wait_ms=5000),
        )

        events = []
        async def collect():
            async for event in ta.subscribe("s1"):
                events.append(event)

        collect_task = asyncio.create_task(collect())

        # First segment — silence fires → INVOKED
        await ta.add_segment("s1", _make_segment("मुझे जॉब चाहिए"))
        await asyncio.sleep(0.1)  # Wait for silence timer to fire

        session = ta._sessions["s1"]
        assert session.current_turn.status == TurnStatus.INVOKED

        # Barge-in: user corrects before first turn completes
        await ta.add_segment("s1", _make_segment("wait wait that is not correct"))

        # Wait for: INTERRUPTED DoneEvent → new turn → silence → new invocation
        await asyncio.sleep(0.3)

        collect_task.cancel()
        try:
            await collect_task
        except asyncio.CancelledError:
            pass

        # New turn should have ONLY the correction — original is discarded.
        assert len(captured_inputs) == 1
        assert captured_inputs[0] == "wait wait that is not correct"


# ---------------------------------------------------------------------------
# GH-137: top-level channels path
# ---------------------------------------------------------------------------


class TestGH137ChannelsPath:

    def test_turn_assembler_reads_top_level_channels(self):
        cfg = {
            "reach_layer": {
                "turn_assembler": {
                    "semantic_gate": {"enabled": True, "confidence_threshold": 0.75},
                    "silence_trigger": {"silence_ms": 400},
                    "max_wait_ceiling": {"max_wait_ms": 8000},
                }
            },
            "channels": {
                "voice": {
                    "turn_assembler": {
                        "semantic_gate": {"enabled": False, "confidence_threshold": 0.9},
                    }
                }
            },
        }
        ta = _make_assembler(config=cfg)
        policy = ta._resolve_config("voice")
        assert policy["semantic_gate"]["enabled"] is False
        assert policy["semantic_gate"]["confidence_threshold"] == 0.9

    def test_turn_assembler_rejects_legacy_reach_layer_channels(self):
        cfg = {
            "reach_layer": {
                "turn_assembler": {"silence_trigger": {"silence_ms": 400}},
                "channels": {"voice": {"turn_assembler": {"silence_trigger": {"silence_ms": 200}}}},
            },
        }
        with pytest.raises(ValueError, match="reach_layer.channels"):
            _make_assembler(config=cfg)


# ---------------------------------------------------------------------------
# GH-149: proactive opening_phrase emission on subscribe
# ---------------------------------------------------------------------------


def _make_opening_phrase_workflow(start_id="greeting", opening_phrase="Hello!"):
    """Build a minimal workflow mock with a start subagent carrying opening_phrase."""
    subagent = MagicMock()
    subagent.opening_phrase = opening_phrase
    workflow = MagicMock()
    workflow.start_subagent_id = start_id
    workflow.subagents = {start_id: subagent}
    return workflow


def _make_opening_phrase_memory(session_state=None):
    """Build an AsyncMock memory client returning a ContextBundle with the given session."""
    mem = MagicMock()
    mem.context_bundle = AsyncMock(
        return_value=ContextBundle(session=dict(session_state or {}), profile={}, journey=None)
    )
    mem.write = AsyncMock(return_value=None)
    return mem


async def _drain_until_done(agen, max_events=10):
    """Collect events from subscribe() until DoneEvent is yielded (or max_events)."""
    events = []
    async for ev in agen:
        events.append(ev)
        if isinstance(ev, DoneEvent) or len(events) >= max_events:
            break
    return events


class TestOpeningPhraseOnSubscribe:
    """GH-149: opening_phrase is emitted on first SSE connect for a new session."""

    @pytest.mark.asyncio
    async def test_emits_opening_phrase_on_new_session(self):
        workflow = _make_opening_phrase_workflow(opening_phrase="नमस्ते।")
        memory = _make_opening_phrase_memory(session_state={})
        ta = _make_assembler(workflow=workflow, async_memory=memory)

        events = await _drain_until_done(ta.subscribe("s1", user_id="u1"))

        assert len(events) == 2
        assert isinstance(events[0], SentenceEvent)
        assert events[0].text == "नमस्ते।"
        assert isinstance(events[1], DoneEvent)
        assert events[1].turn_status == "completed"

        # Flag + subagent were persisted before events were enqueued.
        write_calls = {(c.args[2], c.args[3]): c.args[4] for c in memory.write.call_args_list}
        assert write_calls[("session", "opening_phrase_emitted")] is True
        assert write_calls[("session", "current_subagent_id")] == "greeting"

    @pytest.mark.asyncio
    async def test_skips_when_flag_already_set(self):
        """Reconnect case: flag is persisted, so no events are emitted."""
        workflow = _make_opening_phrase_workflow()
        memory = _make_opening_phrase_memory(
            session_state={"opening_phrase_emitted": True, "current_subagent_id": "greeting"}
        )
        ta = _make_assembler(workflow=workflow, async_memory=memory)

        session = ta._get_or_create_session("s1")

        # Call the emission helper directly — if it no-ops, no turn is installed.
        await ta._emit_opening_phrase_if_first("s1", "u1", session)

        assert session.current_turn is None  # No turn was installed
        memory.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_user_id_none(self):
        """Back-compat: callers that don't pass user_id get the old behavior."""
        workflow = _make_opening_phrase_workflow()
        memory = _make_opening_phrase_memory(session_state={})
        ta = _make_assembler(workflow=workflow, async_memory=memory)

        session = ta._get_or_create_session("s1")
        await ta._emit_opening_phrase_if_first("s1", None, session)

        assert session.current_turn is None  # No events emitted
        memory.context_bundle.assert_not_called()
        memory.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_opening_phrase_does_not_latch_flag(self):
        """Empty opening_phrase → no events, and flag must NOT be latched.

        Latching opening_phrase_emitted when no phrase was emitted suppresses
        the greeting for the rest of the session. Concretely, on a callback
        that adopts a fallback subagent (e.g. ``clarification``) whose
        opening_phrase is empty, this would silently start the call. The
        orchestrator's first-turn gate latches the flag on the first user
        turn — that is the right moment for empty-phrase subagents.
        """
        workflow = _make_opening_phrase_workflow(opening_phrase="")
        memory = _make_opening_phrase_memory(session_state={})
        ta = _make_assembler(workflow=workflow, async_memory=memory)

        session = ta._get_or_create_session("s1")
        await ta._emit_opening_phrase_if_first("s1", "u1", session)

        write_keys = {(c.args[2], c.args[3]) for c in memory.write.call_args_list}
        assert ("session", "opening_phrase_emitted") not in write_keys
        # No turn installed since phrase was empty
        assert session.current_turn is None

    @pytest.mark.asyncio
    async def test_skips_when_workflow_missing(self):
        memory = _make_opening_phrase_memory(session_state={})
        ta = _make_assembler(workflow=None, async_memory=memory)

        session = ta._get_or_create_session("s1")
        await ta._emit_opening_phrase_if_first("s1", "u1", session)

        assert session.current_turn is None
        memory.context_bundle.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_bundle_failure_is_graceful(self):
        """Memory read errors must not crash the SSE connection."""
        workflow = _make_opening_phrase_workflow()
        memory = MagicMock()
        memory.context_bundle = AsyncMock(side_effect=RuntimeError("memory down"))
        memory.write = AsyncMock()
        ta = _make_assembler(workflow=workflow, async_memory=memory)

        session = ta._get_or_create_session("s1")
        await ta._emit_opening_phrase_if_first("s1", "u1", session)

        assert session.current_turn is None
        memory.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_consent_prompt_when_consent_pending(self):
        """GH-239: ask_for_consent=true + user_storage_mode unset → emit consent_prompt
        instead of staying silent. Bumps turn_count to 1 so the orchestrator's
        consent gate runs verify_consent against the user's first reply.
        """
        workflow = _make_opening_phrase_workflow(opening_phrase="नमस्ते।")
        memory = _make_opening_phrase_memory(session_state={})  # no user_storage_mode
        cfg = _make_config()
        cfg["agent"] = {
            "ask_for_consent": True,
            "consent_prompt": "क्या मैं याद रख सकती हूँ?",
        }
        ta = _make_assembler(workflow=workflow, async_memory=memory, config=cfg)

        session = ta._get_or_create_session("s1")
        await ta._emit_opening_phrase_if_first("s1", "u1", session)

        # A consent-prompt turn was installed.
        assert session.current_turn is not None
        events = []
        async for ev in session.current_turn.iter_events():
            events.append(ev)
            if isinstance(ev, DoneEvent):
                break
        assert isinstance(events[0], SentenceEvent)
        assert events[0].text == "क्या मैं याद रख सकती हूँ?"
        # Flag latched + turn_count bumped; opening_phrase_emitted left untouched
        # so the post-consent flow can still set it later if needed.
        write_calls = {(c.args[2], c.args[3]): c.args[4] for c in memory.write.call_args_list}
        assert write_calls[("session", "consent_prompt_emitted")] is True
        assert write_calls[("session", "turn_count")] == 1
        assert ("session", "opening_phrase_emitted") not in write_calls

    @pytest.mark.asyncio
    async def test_consent_pending_skips_when_already_emitted(self):
        """GH-239: consent_prompt_emitted flag prevents re-emission on reconnect."""
        workflow = _make_opening_phrase_workflow(opening_phrase="नमस्ते।")
        memory = _make_opening_phrase_memory(
            session_state={"consent_prompt_emitted": True}
        )
        cfg = _make_config()
        cfg["agent"] = {
            "ask_for_consent": True,
            "consent_prompt": "क्या मैं याद रख सकती हूँ?",
        }
        ta = _make_assembler(workflow=workflow, async_memory=memory, config=cfg)

        session = ta._get_or_create_session("s1")
        await ta._emit_opening_phrase_if_first("s1", "u1", session)

        assert session.current_turn is None
        memory.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_consent_pending_with_empty_prompt_stays_silent(self):
        """GH-239: misconfigured ask_for_consent=true + empty consent_prompt
        falls back to suppress-and-stay-silent so we don't emit empty audio.
        """
        workflow = _make_opening_phrase_workflow(opening_phrase="नमस्ते।")
        memory = _make_opening_phrase_memory(session_state={})
        cfg = _make_config()
        cfg["agent"] = {"ask_for_consent": True, "consent_prompt": ""}
        ta = _make_assembler(workflow=workflow, async_memory=memory, config=cfg)

        session = ta._get_or_create_session("s1")
        await ta._emit_opening_phrase_if_first("s1", "u1", session)

        assert session.current_turn is None
        memory.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_when_consent_required_but_already_granted(self):
        """ask_for_consent=true but user_storage_mode set → emit normally (reconnect after consent)."""
        workflow = _make_opening_phrase_workflow(opening_phrase="नमस्ते।")
        memory = _make_opening_phrase_memory(session_state={"user_storage_mode": "saved"})
        cfg = _make_config()
        cfg["agent"] = {"ask_for_consent": True}
        ta = _make_assembler(workflow=workflow, async_memory=memory, config=cfg)

        session = ta._get_or_create_session("s1")
        await ta._emit_opening_phrase_if_first("s1", "u1", session)

        assert session.current_turn is not None
        write_keys = {(c.args[2], c.args[3]) for c in memory.write.call_args_list}
        assert ("session", "opening_phrase_emitted") in write_keys

    @pytest.mark.asyncio
    async def test_consent_disabled_emits_on_connect(self):
        """ask_for_consent=false → preserve GH-149 behaviour (emit at SSE connect)."""
        workflow = _make_opening_phrase_workflow(opening_phrase="नमस्ते।")
        memory = _make_opening_phrase_memory(session_state={})
        cfg = _make_config()
        cfg["agent"] = {"ask_for_consent": False}
        ta = _make_assembler(workflow=workflow, async_memory=memory, config=cfg)

        session = ta._get_or_create_session("s1")
        await ta._emit_opening_phrase_if_first("s1", "u1", session)

        assert session.current_turn is not None
        write_keys = {(c.args[2], c.args[3]) for c in memory.write.call_args_list}
        assert ("session", "opening_phrase_emitted") in write_keys

    @pytest.mark.asyncio
    async def test_subscribe_emits_opening_phrase_end_to_end(self):
        """subscribe() drains the emitted events via the real event loop."""
        workflow = _make_opening_phrase_workflow(opening_phrase="नमस्ते।")
        memory = _make_opening_phrase_memory(session_state={})
        ta = _make_assembler(workflow=workflow, async_memory=memory)

        events = []
        async for event in ta.subscribe("s1", user_id="u1"):
            events.append(event)
            if isinstance(event, DoneEvent):
                break

        assert [type(e).__name__ for e in events] == ["SentenceEvent", "DoneEvent"]
        assert events[0].text == "नमस्ते।"


# ---------------------------------------------------------------------------
# Session/Turn refactor tests (#224)
# ---------------------------------------------------------------------------


class TestSessionTurnRefactor:
    """Tests for the Session/Turn internal model introduced in #224."""

    @pytest.mark.asyncio
    async def test_add_segment_creates_session_lazily(self):
        """First add_segment for a session creates a Session and an active Turn."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        seg = SegmentInput(text="hi", channel="cli")
        await ta.add_segment("s1", seg)
        assert "s1" in ta._sessions
        session = ta._sessions["s1"]
        from src.session import Session as SessionClass
        from src.turn import Turn as TurnClass, TurnStatus as TS
        assert isinstance(session, SessionClass)
        assert session.current_turn is not None
        assert isinstance(session.current_turn, TurnClass)
        assert any(s.text == "hi" for s in session.current_turn.segments)

    @pytest.mark.asyncio
    async def test_add_segment_after_terminal_turn_installs_new_turn(self):
        """If current_turn is terminal, add_segment installs a new Turn."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", SegmentInput(text="a", channel="cli"))
        session = ta._sessions["s1"]
        first = session.current_turn
        first.status = TurnStatus.COMPLETED  # simulate natural completion
        await ta.add_segment("s1", SegmentInput(text="b", channel="cli"))
        assert session.current_turn is not first
        assert session.current_turn.epoch > first.epoch

    @pytest.mark.asyncio
    async def test_invoke_passes_abort_event_and_turn_id_to_stream_turn(self):
        """_invoke() passes turn.abort_event and turn.turn_id to stream_turn."""
        captured = {}

        async def fake_stream(turn_input, *, abort_event=None, turn_id=""):
            captured["abort_event"] = abort_event
            captured["turn_id"] = turn_id
            yield DoneEvent(turn_status="completed", turn_id=turn_id)

        agent = MagicMock()
        agent.stream_turn = fake_stream

        ta = _make_assembler(
            agent_core=agent,
            config=_make_config(silence_ms=30, max_wait_ms=5000),
        )
        await ta.add_segment("s1", _make_segment("hi"))
        await asyncio.sleep(0.15)

        session = ta._sessions["s1"]
        turn = session.current_turn
        assert captured.get("turn_id") == turn.turn_id
        assert captured.get("abort_event") is turn.abort_event

    @pytest.mark.asyncio
    async def test_cancel_seals_turn_queue_and_signals_abort(self):
        """cancel() sets abort_event and marks turn INTERRUPTED/ABANDONED."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hi"))
        session = ta._sessions["s1"]
        turn = session.current_turn
        assert turn is not None

        await ta.cancel("s1")

        assert turn.abort_event.is_set()
        assert turn.status in (TurnStatus.INTERRUPTED, TurnStatus.ABANDONED)

    @pytest.mark.asyncio
    async def test_cancel_is_idempotent(self):
        """cancel() called twice does not enqueue a second DoneEvent."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hi"))
        await ta.cancel("s1")
        qsize_after_first = ta._sessions["s1"].current_turn.event_queue.qsize()
        await ta.cancel("s1")
        assert ta._sessions["s1"].current_turn.event_queue.qsize() == qsize_after_first

    @pytest.mark.asyncio
    async def test_cancel_on_unknown_session_is_noop(self):
        """cancel() on unknown session_id does not raise."""
        ta = _make_assembler()
        await ta.cancel("does-not-exist")  # must not raise

    @pytest.mark.asyncio
    async def test_subscribe_rolls_over_to_new_turn_after_done(self):
        """subscribe() delivers events from Turn 1 then Turn 2 in sequence."""
        call = {"n": 0}

        async def stream(turn_input, *, abort_event=None, turn_id=""):
            call["n"] += 1
            yield SentenceEvent(text=f"reply{call['n']}", sentence_index=0, turn_id=turn_id)
            yield DoneEvent(turn_status="completed", turn_id=turn_id)

        agent = MagicMock()
        agent.stream_turn = stream

        ta = _make_assembler(
            agent_core=agent,
            config=_make_config(silence_ms=30, max_wait_ms=5000),
        )

        received = []

        async def consume():
            async for ev in ta.subscribe("s1"):
                received.append(ev)
                if len(received) >= 4:
                    break

        consumer = asyncio.create_task(consume())
        await ta.add_segment("s1", _make_segment("q1"))
        # Wait for first Done in received
        for _ in range(50):
            if any(isinstance(e, DoneEvent) for e in received):
                break
            await asyncio.sleep(0.01)
        # Install second turn
        await ta.add_segment("s1", _make_segment("q2"))
        await asyncio.wait_for(consumer, timeout=3.0)

        sentence_texts = [e.text for e in received if isinstance(e, SentenceEvent)]
        assert sentence_texts == ["reply1", "reply2"]

    @pytest.mark.asyncio
    async def test_session_end_cancels_active_turn_and_wakes_subscriber(self):
        """session_end() cancels the turn and allows the subscribe loop to exit."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hi"))
        received = []
        finished = asyncio.Event()

        async def consume():
            try:
                async for ev in ta.subscribe("s1"):
                    received.append(ev)
            finally:
                finished.set()

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        await ta.session_end("s1")
        await asyncio.wait_for(finished.wait(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_subscribe_blocks_until_first_add_segment_with_no_opening_phrase(self):
        """Subscribe connected before any add_segment, with no opening phrase, blocks
        on session.turn_changed until the first segment installs a Turn."""
        ta = _make_assembler()  # default mock has no opening_phrase
        received = []
        started = asyncio.Event()

        async def consume():
            started.set()
            async for ev in ta.subscribe("s1"):
                received.append(ev)
                if isinstance(ev, DoneEvent):
                    break

        consumer = asyncio.create_task(consume())
        await started.wait()
        # Brief yield: subscribe should now be inside turn_changed.wait()
        await asyncio.sleep(0.02)
        assert received == [], (
            f"subscribe should block when no Turn exists; received: {received}"
        )
        # Now produce a segment — silence_trigger fires and installs a Turn
        await ta.add_segment("s1", _make_segment(text="hi"))
        await asyncio.wait_for(consumer, timeout=2.0)
        assert any(isinstance(ev, DoneEvent) for ev in received)

    @pytest.mark.asyncio
    async def test_concurrent_cancels_produce_exactly_one_terminal_done(self):
        """Two simultaneous cancel() calls on the same session must produce
        exactly one terminal DoneEvent (idempotency under concurrency)."""
        ta = _make_assembler()
        await ta.add_segment("s1", _make_segment(text="hi"))
        # Force INVOKED so cancel takes the interrupted-path
        from src.session import Session as SessionClass
        from src.turn import Turn as TurnClass, TurnStatus as TS

        session = ta._sessions["s1"]
        session.current_turn.status = TS.INVOKED
        # Fire two cancels concurrently
        await asyncio.gather(ta.cancel("s1"), ta.cancel("s1"))
        turn = session.current_turn
        # Drain the queue and count DoneEvents
        dones = []
        while not turn.event_queue.empty():
            ev = turn.event_queue.get_nowait()
            if isinstance(ev, DoneEvent):
                dones.append(ev)
        assert len(dones) == 1, (
            f"concurrent cancel should produce exactly 1 DoneEvent; got {len(dones)}: {dones}"
        )
        assert dones[0].turn_status == "interrupted"
        assert turn.status == TS.INTERRUPTED

    @pytest.mark.asyncio
    async def test_invoke_does_not_push_completed_done_after_cancel(self):
        """Status guard in _invoke prevents Done(completed) from reaching the
        queue after cancel() has already pushed Done(interrupted).

        The race: cancel() sets turn.status=INTERRUPTED and pushes
        Done(interrupted) to the queue BEFORE _invoke's loop processes the
        final Done(completed) event from the generator. Without the
        ``turn.status != INVOKED`` guard, _invoke sees abort_event unset at
        its check (generator already yielded Done synchronously), then
        cancel fires between the check and the put, resulting in both
        Done(completed) and Done(interrupted) in the queue.

        To make the race deterministic in a single-threaded event loop, we
        drive _invoke directly without task.cancel() involvement: we use a
        synchronous-yield generator so no CancelledError interrupts the flow,
        manually transition the turn state as cancel() would, and verify that
        _invoke's status guard catches the stale Done.
        """
        # --- Build a Turn manually and drive _invoke directly ----------------
        # We bypass the silence-trigger machinery so we can control timing.
        # The generator yields Done(completed) synchronously (no internal await
        # after the sentence), so _invoke processes it without a task-cancel
        # interruption — reproducing the race where abort_event.is_set() was
        # False when checked but cancel has since transitioned the turn.

        # Shared state: we'll inject the race by patching the abort_event
        # check window. We do this by replacing abort_event.is_set with a
        # version that, on first call, simulates cancel() firing: it sets
        # status=INTERRUPTED, pushes Done(interrupted), THEN returns False
        # (the pre-cancel snapshot). On second call it returns True.
        agent = MagicMock()

        async def sync_done_stream(turn_input, *, abort_event=None, turn_id=""):
            """Generator that yields Done(completed) without any internal await
            — simulating a fast, synchronous completion path where the task
            cancel has no await to land on between the abort check and the put.
            """
            yield SentenceEvent(text="hello", sentence_index=0, turn_id=turn_id)
            yield DoneEvent(turn_status="completed", turn_id=turn_id)

        agent.stream_turn = sync_done_stream
        ta = _make_assembler(agent_core=agent)

        # Create a session and its initial turn via add_segment.
        # Use a very long silence_ms so the timer doesn't auto-invoke.
        ta._config["reach_layer"]["turn_assembler"]["silence_ms"] = 30_000
        ta._config["reach_layer"]["turn_assembler"]["max_wait_ms"] = 30_000
        await ta.add_segment("s1", _make_segment(text="hi"))

        session = ta._sessions["s1"]
        turn = session.current_turn

        # Manually put the turn in INVOKED state (skip the silence trigger).
        turn.status = TurnStatus.INVOKED

        # Intercept abort_event.is_set() so that on the SECOND call (when
        # _invoke is checking after receiving Done(completed)), we simulate
        # cancel() having already fired: set status=INTERRUPTED and put
        # Done(interrupted) in the queue, then return False (the snapshot
        # value _invoke would have seen had the race not been guarded).
        # This faithfully represents: "cancel ran between the is_set() check
        # and the queue.put(), but _invoke already read is_set()=False."
        call_count = 0
        original_is_set = turn.abort_event.is_set

        async def inject_cancel_and_put_interrupted():
            """Simulate cancel() mid-flight: mutate state as cancel() does."""
            turn.status = TurnStatus.INTERRUPTED
            turn.abort_event.set()
            await turn.event_queue.put(
                DoneEvent(turn_status="interrupted", turn_id=turn.turn_id)
            )

        def patched_is_set():
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                # This is the check inside the _invoke loop for Done(completed).
                # Simulate the race: cancel just ran. We mutate synchronously
                # (event.set() is synchronous) to mimic the state _invoke would
                # see on the next check — but return False as the pre-cancel
                # snapshot (the value _invoke already read before cancel ran).
                turn.status = TurnStatus.INTERRUPTED
                # NOTE: we do NOT set abort_event here — we return False to
                # represent the race window where abort_event.is_set() returned
                # False but cancel then ran. The status guard is what we test.
                return False
            return original_is_set()

        turn.abort_event.is_set = patched_is_set

        # Also push Done(interrupted) as cancel() would have done.
        await turn.event_queue.put(
            DoneEvent(turn_status="interrupted", turn_id=turn.turn_id)
        )

        # Drive _invoke directly (no task wrapping, no CancelledError).
        await ta._invoke(turn)

        # Drain the queue and inspect DoneEvents.
        drained = []
        while not turn.event_queue.empty():
            drained.append(turn.event_queue.get_nowait())

        dones = [e for e in drained if isinstance(e, DoneEvent)]
        # With the status guard: _invoke sees turn.status == INTERRUPTED on the
        # second iteration and returns WITHOUT putting Done(completed). Only
        # Done(interrupted) (pre-injected above) remains.
        # Without the guard: _invoke would put Done(completed) after the
        # interrupted Done, giving 2 DoneEvents with Done(completed) first
        # (since queue ordering: interrupted was put first, then completed).
        assert len(dones) == 1, (
            f"expected exactly 1 DoneEvent (interrupted, pre-queued by cancel); "
            f"got {len(dones)}: {[d.turn_status for d in dones]}"
        )
        assert dones[0].turn_status == "interrupted", (
            f"expected interrupted, got {dones[0].turn_status}"
        )
        assert turn.status == TurnStatus.INTERRUPTED
