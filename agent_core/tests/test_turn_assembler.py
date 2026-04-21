"""
Tests for TurnAssembler (#72): core classes, policy stack, buffer management.

Covers:
  - TurnStatus state machine
  - SessionBuffer creation and reset
  - TurnAssemblerBase ABC enforcement
  - TurnAssembler: add_segment, subscribe, cancel, session_end
  - Policy stack: silence trigger, max wait ceiling, semantic gate
  - Invocation path: stream_turn() called directly
  - Memory consistency on cancellation (#83)
  - Edge cases: empty text, missing session, concurrent timers
"""

import asyncio
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
    SessionBuffer,
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

    async def _stream(turn_input):
        yield SignalEvent(stage="memory_read", status="start")
        yield SentenceEvent(text="Hello!", sentence_index=0)
        yield DoneEvent(turn_id="t-1", turn_status="completed")

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
# SessionBuffer
# ---------------------------------------------------------------------------


class TestSessionBuffer:

    def test_default_creation(self):
        buf = SessionBuffer(session_id="s1")
        assert buf.session_id == "s1"
        assert buf.segments == []
        assert buf.status == TurnStatus.WAITING
        assert buf.silence_task is None
        assert buf.ceiling_task is None
        assert buf.invocation_task is None
        assert buf.context_bundle is None
        assert buf._context_fetched is False

    def test_created_at_ms_is_set(self):
        buf = SessionBuffer(session_id="s1")
        assert buf.created_at_ms > 0


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
        assert ta._sessions["s1"].segments == ["hello"]

    @pytest.mark.asyncio
    async def test_appends_to_existing_buffer(self):
        ta = _make_assembler()
        await ta.add_segment("s1", _make_segment("hello"))
        await ta.add_segment("s1", _make_segment("world"))
        assert ta._sessions["s1"].segments == ["hello", "world"]

    @pytest.mark.asyncio
    async def test_caches_metadata_from_first_segment(self):
        ta = _make_assembler()
        seg = _make_segment("hi", channel="voice", user_id="u42", timestamp_ms=5000)
        await ta.add_segment("s1", seg)
        buf = ta._sessions["s1"]
        assert buf.channel == "voice"
        assert buf.user_id == "u42"
        assert buf.first_timestamp_ms == 5000

    @pytest.mark.asyncio
    async def test_segment_triggers_barge_in_when_invoked(self):
        """New segment while INVOKED queues to pending and cancels current turn."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hello"))
        buf = ta._sessions["s1"]
        # Simulate turn in flight
        buf.status = TurnStatus.INVOKED
        buf.invocation_task = asyncio.create_task(asyncio.sleep(10))

        await ta.add_segment("s1", _make_segment("new message"))

        # Segment queued as pending, turn cancelled
        assert len(buf.pending_segments) == 1
        assert buf.pending_segments[0].text == "new message"
        assert buf.status == TurnStatus.INTERRUPTED

    @pytest.mark.asyncio
    async def test_segment_ignored_when_completed(self):
        """Segment arriving when COMPLETED is still ignored (not barge-in)."""
        ta = _make_assembler()
        await ta.add_segment("s1", _make_segment("hello"))
        ta._sessions["s1"].status = TurnStatus.COMPLETED
        await ta.add_segment("s1", _make_segment("ignored"))
        assert len(ta._sessions["s1"].segments) == 1
        assert len(ta._sessions["s1"].pending_segments) == 0

    @pytest.mark.asyncio
    async def test_strips_whitespace_from_text(self):
        ta = _make_assembler()
        await ta.add_segment("s1", _make_segment("  hello  "))
        assert ta._sessions["s1"].segments == ["hello"]


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

        buf = ta._sessions.get("s1")
        # Buffer should have been invoked (status may be COMPLETED by now)
        assert buf is not None
        assert buf.status in (TurnStatus.INVOKED, TurnStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_silence_timer_resets_on_new_segment(self):
        """Adding a new segment resets the silence timer."""
        ta = _make_assembler(config=_make_config(silence_ms=80, max_wait_ms=5000))

        await ta.add_segment("s1", _make_segment("hello"))
        await asyncio.sleep(0.04)  # 40ms — timer not yet fired
        await ta.add_segment("s1", _make_segment("world"))
        await asyncio.sleep(0.04)  # another 40ms — timer reset, still hasn't fired

        buf = ta._sessions["s1"]
        assert buf.status == TurnStatus.WAITING
        assert len(buf.segments) == 2


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

        buf = ta._sessions.get("s1")
        assert buf is not None
        assert buf.status in (TurnStatus.INVOKED, TurnStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_ceiling_with_no_segments_abandons(self):
        """Ceiling timer firing with no segments transitions to ABANDONED."""
        ta = _make_assembler(config=_make_config(max_wait_ms=30))

        # Create buffer directly but don't add segments
        buf = SessionBuffer(session_id="s1")
        ta._sessions["s1"] = buf
        buf.ceiling_task = asyncio.create_task(ta._ceiling_timer("s1", 30))

        await asyncio.sleep(0.1)

        assert buf.status == TurnStatus.ABANDONED

    @pytest.mark.asyncio
    async def test_ceiling_never_resets(self):
        """Ceiling timer is started once and never reset by new segments."""
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=100))

        await ta.add_segment("s1", _make_segment("hello"))
        ceiling_task = ta._sessions["s1"].ceiling_task

        await ta.add_segment("s1", _make_segment("world"))
        # Ceiling task should be the same object — not recreated
        assert ta._sessions["s1"].ceiling_task is ceiling_task


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

        buf = ta._sessions.get("s1")
        assert buf is not None
        assert buf.status in (TurnStatus.INVOKED, TurnStatus.COMPLETED)

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
        buf = ta._sessions["s1"]
        assert buf.status == TurnStatus.WAITING

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
        buf = ta._sessions["s1"]
        assert buf.status == TurnStatus.WAITING

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
        buf = ta._sessions["s1"]
        assert buf.status == TurnStatus.WAITING  # Fell through

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
    async def test_subscribe_creates_buffer_if_needed(self):
        """subscribe() creates a buffer if one doesn't exist."""
        ta = _make_assembler()

        # Put events manually to simulate invocation
        ta._sessions["s1"] = SessionBuffer(session_id="s1")
        await ta._sessions["s1"].event_queue.put(DoneEvent(turn_status="completed"))

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
    async def test_subscribe_resets_buffer_after_done(self):
        """After DoneEvent, buffer is reset to WAITING for next turn.

        subscribe() loops (while True) so it resets and then blocks on the new
        empty queue. We cancel the task after the reset has had time to execute.
        """
        ta = _make_assembler()

        ta._sessions["s1"] = SessionBuffer(session_id="s1")
        ta._sessions["s1"].status = TurnStatus.COMPLETED
        await ta._sessions["s1"].event_queue.put(DoneEvent(turn_status="completed"))

        async def drain():
            async for _ in ta.subscribe("s1"):
                pass

        drain_task = asyncio.create_task(drain())
        await asyncio.sleep(0.2)  # Allow DoneEvent to be processed and buffer reset
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass

        # Buffer should still exist but be reset to WAITING
        assert "s1" in ta._sessions
        assert ta._sessions["s1"].status == TurnStatus.WAITING
        assert ta._sessions["s1"].segments == []

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
        # Cancel while silence timer is still waiting
        ta._sessions["s1"].silence_task.cancel()  # Stop timer to prevent race
        ta._sessions["s1"].status = TurnStatus.WAITING

        await ta.cancel("s1")

        assert ta._sessions["s1"].status == TurnStatus.ABANDONED

    @pytest.mark.asyncio
    async def test_cancel_waiting_pushes_done_event(self):
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hello"))

        await ta.cancel("s1")

        event = await ta._sessions["s1"].event_queue.get()
        assert isinstance(event, DoneEvent)
        assert event.turn_status == "abandoned"

    @pytest.mark.asyncio
    async def test_cancel_invoked_transitions_to_interrupted(self):
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
        await ta.add_segment("s1", _make_segment("hello"))
        buf = ta._sessions["s1"]
        buf.status = TurnStatus.INVOKED
        buf.invocation_task = asyncio.create_task(asyncio.sleep(10))

        await ta.cancel("s1")

        assert buf.status == TurnStatus.INTERRUPTED
        event = await buf.event_queue.get()
        assert isinstance(event, DoneEvent)
        assert event.turn_status == "interrupted"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_session_noop(self):
        ta = _make_assembler()
        await ta.cancel("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_cancel_completed_noop(self):
        ta = _make_assembler()
        ta._sessions["s1"] = SessionBuffer(session_id="s1")
        ta._sessions["s1"].status = TurnStatus.COMPLETED

        await ta.cancel("s1")
        assert ta._sessions["s1"].status == TurnStatus.COMPLETED  # Unchanged


# ---------------------------------------------------------------------------
# session_end
# ---------------------------------------------------------------------------


class TestSessionEnd:

    @pytest.mark.asyncio
    async def test_session_end_removes_buffer(self):
        ta = _make_assembler()
        ta._sessions["s1"] = SessionBuffer(session_id="s1")

        await ta.session_end("s1")
        assert "s1" not in ta._sessions

    @pytest.mark.asyncio
    async def test_session_end_nonexistent_noop(self):
        ta = _make_assembler()
        await ta.session_end("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_session_end_cancels_tasks(self):
        ta = _make_assembler()
        buf = SessionBuffer(session_id="s1")
        silence = asyncio.create_task(asyncio.sleep(10))
        ceiling = asyncio.create_task(asyncio.sleep(10))
        invocation = asyncio.create_task(asyncio.sleep(10))
        buf.silence_task = silence
        buf.ceiling_task = ceiling
        buf.invocation_task = invocation
        ta._sessions["s1"] = buf

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

        buf = ta._sessions.get("s1")
        assert buf is not None
        assert buf.status == TurnStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_invoke_assembles_text(self):
        """Multiple segments are joined with spaces."""
        captured_inputs = []

        async def capture_stream(turn_input):
            captured_inputs.append(turn_input)
            yield DoneEvent(turn_status="completed")

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
        async def failing_stream(turn_input):
            raise RuntimeError("boom")
            yield  # Make it a generator

        agent = MagicMock()
        agent.stream_turn = failing_stream

        ta = _make_assembler(agent_core=agent, config=_make_config(silence_ms=30))

        await ta.add_segment("s1", _make_segment("hello"))
        await asyncio.sleep(0.15)

        buf = ta._sessions.get("s1")
        assert buf is not None
        event = await buf.event_queue.get()
        assert isinstance(event, DoneEvent)
        assert event.turn_status == "abandoned"

    @pytest.mark.asyncio
    async def test_invoke_uses_first_segment_metadata(self):
        """TurnInput is constructed with first segment's channel, user_id, timestamp."""
        captured = []

        async def capture_stream(turn_input):
            captured.append(turn_input)
            yield DoneEvent(turn_status="completed")

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
        assert ti.timestamp_ms == 42000


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
        assert ta._sessions["s1"].context_bundle is not None

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

        buf = ta._sessions["s1"]
        assert buf._context_fetched is True
        assert buf.context_bundle is None  # Failed gracefully
        assert buf.status == TurnStatus.WAITING  # Still operational

    @pytest.mark.asyncio
    async def test_no_async_memory_skips_fetch(self):
        ta = _make_assembler(
            config=_make_config(silence_ms=5000, max_wait_ms=5000),
            async_memory=None,
        )

        await ta.add_segment("s1", _make_segment("hello"))
        assert ta._sessions["s1"]._context_fetched is False


# ---------------------------------------------------------------------------
# Buffer management
# ---------------------------------------------------------------------------


class TestBufferManagement:

    @pytest.mark.asyncio
    async def test_reset_buffer_preserves_identity(self):
        ta = _make_assembler()
        buf = SessionBuffer(
            session_id="s1", channel="voice", user_id="u1",
        )
        buf.segments = ["hello", "world"]
        buf.status = TurnStatus.COMPLETED
        buf.context_bundle = ContextBundle.empty()

        ta._reset_buffer(buf)

        assert buf.session_id == "s1"
        assert buf.channel == "voice"
        assert buf.user_id == "u1"
        assert buf.context_bundle is not None  # Preserved
        assert buf.segments == []
        assert buf.status == TurnStatus.WAITING

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self):
        ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))

        await ta.add_segment("s1", _make_segment("hello"))
        await ta.add_segment("s2", _make_segment("world"))

        assert "s1" in ta._sessions
        assert "s2" in ta._sessions
        assert ta._sessions["s1"].segments == ["hello"]
        assert ta._sessions["s2"].segments == ["world"]


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

        buf = ta._sessions.get("s1")
        assert buf is not None
        # Should be INVOKED or COMPLETED, not stuck
        assert buf.status in (TurnStatus.INVOKED, TurnStatus.COMPLETED)


# ---------------------------------------------------------------------------
# End-to-end: segment → subscribe → events
# ---------------------------------------------------------------------------


class TestEndToEnd:

    @pytest.mark.asyncio
    async def test_segment_to_events_flow(self):
        """Full flow: add segment → silence triggers → subscribe yields events.

        subscribe() has while True for multi-turn SSE — cancel the task once
        the DoneEvent has been received.
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

        GH-152 Phase 2: the original interrupted segment is discarded. Scenario:
        user says "मुझे जॉब चाहिए" (turn starts → LLM begins responding), then
        barges in with "wait wait that is not correct". The LLM had already
        started on the original, so the caller's correction is reacting to
        that partial output; replaying the original alongside would produce
        the noisy prompt "मुझे जॉब चाहिए wait wait that is not correct".
        Only the correction carries forward as the next turn's input.
        """
        captured_inputs = []

        call_count = 0

        async def slow_then_capture(turn_input):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First invocation: slow enough to be interrupted
                await asyncio.sleep(2)
                yield DoneEvent(turn_status="completed")
            else:
                # Second invocation: capture assembled_text and complete
                captured_inputs.append(turn_input.user_message)
                yield DoneEvent(turn_status="completed")

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

        assert ta._sessions["s1"].status == TurnStatus.INVOKED

        # Barge-in: user corrects before first turn completes
        await ta.add_segment("s1", _make_segment("wait wait that is not correct"))

        # Wait for: INTERRUPTED DoneEvent → reset → replay pending only → silence → new invocation
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

        # Drain without blocking: put a sentinel DoneEvent manually and expect only it.
        buffer = ta._sessions.setdefault("s1", SessionBuffer(session_id="s1"))

        # Call the emission helper directly — if it no-ops, queue stays empty.
        await ta._emit_opening_phrase_if_first("s1", "u1", buffer)

        assert buffer.event_queue.qsize() == 0
        memory.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_user_id_none(self):
        """Back-compat: callers that don't pass user_id get the old behavior."""
        workflow = _make_opening_phrase_workflow()
        memory = _make_opening_phrase_memory(session_state={})
        ta = _make_assembler(workflow=workflow, async_memory=memory)

        buffer = ta._sessions.setdefault("s1", SessionBuffer(session_id="s1"))
        await ta._emit_opening_phrase_if_first("s1", None, buffer)

        assert buffer.event_queue.qsize() == 0
        memory.context_bundle.assert_not_called()
        memory.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_opening_phrase_still_sets_flag(self):
        """Empty opening_phrase on start subagent → flag set, no events emitted."""
        workflow = _make_opening_phrase_workflow(opening_phrase="")
        memory = _make_opening_phrase_memory(session_state={})
        ta = _make_assembler(workflow=workflow, async_memory=memory)

        buffer = ta._sessions.setdefault("s1", SessionBuffer(session_id="s1"))
        await ta._emit_opening_phrase_if_first("s1", "u1", buffer)

        assert buffer.event_queue.qsize() == 0
        # Flag write still happened so the orchestrator gate won't fire on turn 1.
        write_keys = {(c.args[2], c.args[3]) for c in memory.write.call_args_list}
        assert ("session", "opening_phrase_emitted") in write_keys

    @pytest.mark.asyncio
    async def test_skips_when_workflow_missing(self):
        memory = _make_opening_phrase_memory(session_state={})
        ta = _make_assembler(workflow=None, async_memory=memory)

        buffer = ta._sessions.setdefault("s1", SessionBuffer(session_id="s1"))
        await ta._emit_opening_phrase_if_first("s1", "u1", buffer)

        assert buffer.event_queue.qsize() == 0
        memory.context_bundle.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_bundle_failure_is_graceful(self):
        """Memory read errors must not crash the SSE connection."""
        workflow = _make_opening_phrase_workflow()
        memory = MagicMock()
        memory.context_bundle = AsyncMock(side_effect=RuntimeError("memory down"))
        memory.write = AsyncMock()
        ta = _make_assembler(workflow=workflow, async_memory=memory)

        buffer = ta._sessions.setdefault("s1", SessionBuffer(session_id="s1"))
        await ta._emit_opening_phrase_if_first("s1", "u1", buffer)

        assert buffer.event_queue.qsize() == 0
        memory.write.assert_not_called()

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
