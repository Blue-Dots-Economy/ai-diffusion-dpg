"""
Tests for stream_turn() orchestrator method and sentence splitter.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import (
    ContextBundle,
    DoneEvent,
    NLUResult,
    SentenceEvent,
    SignalEvent,
    ToolCall,
    ToolResult,
    TrustCheckResult,
    TurnInput,
)
from src.orchestrator import AgentCore, _split_sentences
from src.exceptions import ToolUseRequested


# ---------------------------------------------------------------------------
# Sentence splitter unit tests
# ---------------------------------------------------------------------------


class TestSplitSentences:

    def test_no_boundary(self):
        sentences, remainder = _split_sentences("Hello world")
        assert sentences == []
        assert remainder == "Hello world"

    def test_single_sentence(self):
        sentences, remainder = _split_sentences("Hello world. ")
        assert sentences == ["Hello world."]
        assert remainder == ""

    def test_two_sentences(self):
        sentences, remainder = _split_sentences("First sentence. Second sentence. ")
        assert sentences == ["First sentence.", "Second sentence."]
        assert remainder == ""

    def test_incomplete_trailing(self):
        sentences, remainder = _split_sentences("First. Second part still going")
        assert sentences == ["First."]
        assert remainder == "Second part still going"

    def test_question_mark(self):
        sentences, remainder = _split_sentences("How are you? I'm fine. ")
        assert len(sentences) == 2
        assert "How are you?" in sentences[0]

    def test_exclamation_mark(self):
        sentences, remainder = _split_sentences("Wow! That's great. ")
        assert len(sentences) == 2

    def test_devanagari_danda(self):
        sentences, remainder = _split_sentences("यह पहला वाक्य है। दूसरा वाक्य। तीसरा")
        assert len(sentences) == 2
        assert "पहला" in sentences[0]
        assert "दूसरा" in sentences[1]
        assert "तीसरा" in remainder

    def test_fullwidth_question(self):
        sentences, remainder = _split_sentences("何ですか？ 答えは？ 続き")
        assert len(sentences) == 2
        assert "何ですか？" == sentences[0]
        assert "答えは？" == sentences[1]
        assert "続き" in remainder

    def test_empty_string(self):
        sentences, remainder = _split_sentences("")
        assert sentences == []
        assert remainder == ""

    def test_whitespace_only(self):
        sentences, remainder = _split_sentences("   ")
        assert sentences == []
        assert remainder == "   "


# ---------------------------------------------------------------------------
# stream_turn() integration tests
# ---------------------------------------------------------------------------

def _make_turn_input(**overrides):
    defaults = {
        "session_id": "sess-1",
        "user_message": "Hello",
        "channel": "cli",
        "timestamp_ms": 1000,
        "user_id": "user-1",
    }
    defaults.update(overrides)
    return TurnInput(**defaults)


def _make_workflow():
    """Build a minimal AgentWorkflow mock."""
    from src.workflow_loader import AgentWorkflow, SubAgent
    sub = SubAgent(
        id="start",
        name="Start",
        description="Start subagent",
        is_start=True,
        is_terminal=False,
        system_prompt="You are helpful.",
        routing=[],
        tools=[],
        special_handler=None,
        valid_intents=["greeting"],
        output_format=None,
    )
    wf = MagicMock(spec=AgentWorkflow)
    wf.start_subagent_id = "start"
    wf.subagents = {"start": sub}
    wf.nlu_intent_set = {"start": ["greeting"]}
    wf.tool_defs = {"start": []}
    wf.global_routing = []
    wf.default_fallback_subagent_id = "start"
    wf.agent_system_prompt = "System prompt"
    return wf


def _make_agent_core(**overrides):
    """Create an AgentCore with all mocked dependencies."""
    config = {
        "agent": {
            "primary_model": "test-model",
            "fallback_model": "test-fallback",
        },
        "channels": {
            "cli": {"system_prompt_suffix": ""},
            "voice": {"system_prompt_suffix": ""},
            "web": {"system_prompt_suffix": ""},
        },
        "conversation": {
            "blocked_message": "Blocked.",
            "escalation_message": "Escalated.",
            "output_blocked_message": "Output blocked.",
        },
        "preprocessing": {
            "language_normalisation": {"default_language": "english"},
            "nlu_processor": {"model_override": "haiku", "model": "haiku"},
        },
        "entity_persistence": {"scope": "persistent"},
        "entity_to_profile_field": {},
    }

    llm = MagicMock()
    llm.get_active_model.return_value = "test-model"

    memory = MagicMock()
    trust = MagicMock()
    ke = MagicMock()
    tool_registry = MagicMock()
    tool_registry.get_route.return_value = None
    manager_agent = MagicMock()
    manager_agent.build_system_prompt.return_value = "System prompt"
    manager_agent.build_messages.return_value = [{"role": "user", "content": "Hello"}]
    learning = MagicMock()
    workflow = _make_workflow()

    # Async mocks
    async_memory = AsyncMock()
    async_memory.context_bundle.return_value = ContextBundle(
        session={"current_subagent_id": "start"},
        profile={},
    )
    async_memory.write = AsyncMock()

    async_trust = AsyncMock()
    async_trust.check_input.return_value = TrustCheckResult(passed=True, action="allow")
    async_trust.check_output.return_value = TrustCheckResult(passed=True, action="allow")
    async_trust.verify_consent = AsyncMock(return_value=True)

    async_ke = AsyncMock()
    async_gateway = AsyncMock()
    async_learning = AsyncMock()

    defaults = dict(
        config=config,
        llm_wrapper=llm,
        memory=memory,
        trust=trust,
        knowledge_engine=ke,
        tool_registry=tool_registry,
        manager_agent=manager_agent,
        learning=learning,
        workflow=workflow,
        async_memory=async_memory,
        async_trust=async_trust,
        async_knowledge_engine=async_ke,
        async_gateway=async_gateway,
        async_learning=async_learning,
    )
    defaults.update(overrides)
    return AgentCore(**defaults)


async def _collect_events(agent, turn_input):
    """Consume stream_turn() and return all events."""
    events = []
    async for event in agent.stream_turn(turn_input):
        events.append(event)
    return events


class TestStreamTurnBasic:

    @pytest.mark.asyncio
    async def test_normal_stream_produces_signal_and_done_events(self):
        """Normal stream produces SignalEvents, SentenceEvents, and a DoneEvent."""
        agent = _make_agent_core()

        # Mock stream_call to yield tokens that form two sentences
        async def mock_stream(*args, **kwargs):
            yield "Hello. "
            yield "How can I help? "

        agent._llm.stream_call = mock_stream
        # Patch NLU to return a simple result
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("Hello", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="greeting", entities={}, sentiment="neutral", confidence=0.9
        )

        events = await _collect_events(agent, _make_turn_input())

        # Check event types
        signal_events = [e for e in events if isinstance(e, SignalEvent)]
        sentence_events = [e for e in events if isinstance(e, SentenceEvent)]
        done_events = [e for e in events if isinstance(e, DoneEvent)]

        assert len(signal_events) > 0, "Should have SignalEvents"
        assert len(sentence_events) >= 1, "Should have at least one SentenceEvent"
        assert len(done_events) == 1, "Should have exactly one DoneEvent"
        assert events[-1] == done_events[0], "DoneEvent should be last"
        assert done_events[0].turn_status == "completed"

    @pytest.mark.asyncio
    async def test_trust_input_blocks(self):
        """Trust input block yields blocked message and DoneEvent."""
        agent = _make_agent_core()
        agent._async_trust.check_input.return_value = TrustCheckResult(
            passed=False, action="block", reason="unsafe"
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="unknown", entities={}, sentiment="neutral", confidence=0.5
        )

        events = await _collect_events(agent, _make_turn_input())

        sentence_events = [e for e in events if isinstance(e, SentenceEvent)]
        done_events = [e for e in events if isinstance(e, DoneEvent)]

        assert len(sentence_events) == 1
        assert sentence_events[0].text == "Blocked."
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_trust_input_escalates(self):
        """Trust input escalation yields escalation message and DoneEvent with was_escalated."""
        agent = _make_agent_core()
        agent._async_trust.check_input.return_value = TrustCheckResult(
            passed=False, action="escalate", reason="sensitive"
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="unknown", entities={}, sentiment="neutral", confidence=0.5
        )

        events = await _collect_events(agent, _make_turn_input())

        done_events = [e for e in events if isinstance(e, DoneEvent)]
        assert done_events[0].was_escalated is True

    @pytest.mark.asyncio
    async def test_missing_async_clients_raises(self):
        """stream_turn() raises ValueError if async clients not injected."""
        agent = _make_agent_core(async_memory=None, async_trust=None)

        with pytest.raises(ValueError, match="Async clients"):
            async for _ in agent.stream_turn(_make_turn_input()):
                pass

    @pytest.mark.asyncio
    async def test_validation_errors(self):
        """stream_turn() validates turn_input fields."""
        agent = _make_agent_core()

        with pytest.raises(ValueError, match="turn_input must not be None"):
            async for _ in agent.stream_turn(None):
                pass

        with pytest.raises(ValueError, match="session_id"):
            async for _ in agent.stream_turn(_make_turn_input(session_id="")):
                pass

        with pytest.raises(ValueError, match="user_message"):
            async for _ in agent.stream_turn(_make_turn_input(user_message=None)):
                pass


class TestStreamTurnToolUse:

    @pytest.mark.asyncio
    async def test_tool_use_mid_stream(self):
        """ToolUseRequested triggers tool execution and resume."""
        agent = _make_agent_core()

        call_count = 0

        async def mock_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield "I'll look that up"
                raise ToolUseRequested([
                    ToolCall(tool_name="search", tool_use_id="tu_1", input_params={"q": "test"})
                ])
            else:
                yield "Here's what I found. "

        agent._llm.stream_call = mock_stream
        agent._async_gateway.execute.return_value = ToolResult(
            tool_use_id="tu_1", tool_name="search",
            result={"answer": "42"}, success=True, result_text="42"
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="search", entities={}, sentiment="neutral", confidence=0.9
        )

        events = await _collect_events(agent, _make_turn_input())

        signal_events = [e for e in events if isinstance(e, SignalEvent)]
        tool_start = [e for e in signal_events if e.stage == "tool_start"]
        tool_end = [e for e in signal_events if e.stage == "tool_end"]
        done_events = [e for e in events if isinstance(e, DoneEvent)]

        assert len(tool_start) == 1
        assert len(tool_end) == 1
        assert done_events[0].was_tool_used is True


class TestStreamTurnTrustOutput:

    @pytest.mark.asyncio
    async def test_trust_output_blocks_sentence(self):
        """Trust output block replaces sentence with fallback."""
        agent = _make_agent_core()

        async def mock_stream(*args, **kwargs):
            yield "Bad content here. "

        agent._llm.stream_call = mock_stream
        agent._async_trust.check_output.return_value = TrustCheckResult(
            passed=False, action="block", reason="unsafe"
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="greeting", entities={}, sentiment="neutral", confidence=0.9
        )

        events = await _collect_events(agent, _make_turn_input())

        sentence_events = [e for e in events if isinstance(e, SentenceEvent)]
        assert any("blocked" in e.text.lower() or "safe" in e.text.lower() for e in sentence_events)

        done_events = [e for e in events if isinstance(e, DoneEvent)]
        assert done_events[0].was_escalated is True

    @pytest.mark.asyncio
    async def test_trust_infra_failure_allows_through(self):
        """Trust infra failure treats sentence as allowed (spec requirement)."""
        agent = _make_agent_core()

        async def mock_stream(*args, **kwargs):
            yield "Normal content. "

        agent._llm.stream_call = mock_stream
        agent._async_trust.check_output.side_effect = Exception("Connection refused")
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="greeting", entities={}, sentiment="neutral", confidence=0.9
        )

        events = await _collect_events(agent, _make_turn_input())

        sentence_events = [e for e in events if isinstance(e, SentenceEvent)]
        # Sentence should pass through despite trust infra failure
        assert any("Normal content" in e.text for e in sentence_events)

    @pytest.mark.asyncio
    async def test_exception_produces_abandoned_done_event(self):
        """Unhandled exception in stream_turn() yields DoneEvent with abandoned status."""
        agent = _make_agent_core()
        agent._async_memory.context_bundle.side_effect = RuntimeError("boom")

        events = await _collect_events(agent, _make_turn_input())

        done_events = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done_events) == 1
        assert done_events[0].turn_status == "abandoned"


class TestStreamTurnChannelValidation:

    @pytest.mark.asyncio
    async def test_stream_turn_unsupported_channel_raises_value_error(self):
        """stream_turn raises ValueError before yielding for a channel not in agent.channels."""
        agent = _make_agent_core()
        turn = _make_turn_input(channel="whatsapp")

        with pytest.raises(ValueError, match="Unsupported channel: whatsapp"):
            await _collect_events(agent, turn)

    @pytest.mark.asyncio
    async def test_stream_turn_supported_channel_does_not_raise(self):
        """stream_turn does not raise ValueError for a channel that is in agent.channels."""
        agent = _make_agent_core()

        async def mock_stream(*args, **kwargs):
            yield "Hello. "

        agent._llm.stream_call = mock_stream
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("Hello", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="greeting", entities={}, sentiment="neutral", confidence=0.9
        )

        # "web" is in the config channels — should not raise
        turn = _make_turn_input(channel="web")
        events = await _collect_events(agent, turn)

        done_events = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done_events) == 1



class TestStreamTurnEndSession:
    """GH-191: end_session must set DoneEvent.session_ended=True in streaming."""

    def _make_end_session_agent(self):
        agent = _make_agent_core()
        # GH-204: these tests exercise the LLM tool-loop end_session path —
        # disable the termination short-circuit so the high-confidence NLU
        # below doesn't bypass the path under test.
        agent._config["agent"]["termination_short_circuit"] = {"enabled": False}
        # Manager agent must expose the same attributes the orchestrator
        # touches in the sync path (so the streaming path has parity).
        agent._manager_agent._session_ended_flag = False

        def _reset_flags():
            agent._manager_agent._session_ended_flag = False

        agent._manager_agent._reset_turn_flags = MagicMock(side_effect=_reset_flags)
        # session_ended is read via getattr on the manager — make it reflect
        # the underlying flag.
        type(agent._manager_agent).session_ended = property(
            lambda self: self._session_ended_flag
        )

        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("bye", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="termination_intent", entities={}, sentiment="neutral", confidence=0.95
        )
        return agent

    @pytest.mark.asyncio
    async def test_end_session_tool_sets_session_ended_true(self):
        """A streaming turn whose tool loop contains end_session emits
        DoneEvent(session_ended=True)."""
        agent = self._make_end_session_agent()

        call_count = 0

        async def mock_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield "Goodbye"
                raise ToolUseRequested([
                    ToolCall(
                        tool_name="end_session",
                        tool_use_id="tu_end",
                        input_params={"reason": "user_said_bye"},
                    )
                ])
            else:
                yield "Take care. "

        agent._llm.stream_call = mock_stream

        # Action Gateway must NOT be invoked for end_session — fail loud if it is.
        agent._async_gateway.execute = AsyncMock(
            side_effect=AssertionError("end_session must not be routed to Action Gateway")
        )

        events = await _collect_events(agent, _make_turn_input())

        done_events = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done_events) == 1
        assert done_events[0].session_ended is True
        assert done_events[0].was_tool_used is True

    @pytest.mark.asyncio
    async def test_session_ended_flag_cleared_between_turns(self):
        """The end_session flag must not leak from one turn into the next."""
        agent = self._make_end_session_agent()

        async def first_stream(*args, **kwargs):
            yield "Bye"
            raise ToolUseRequested([
                ToolCall(
                    tool_name="end_session", tool_use_id="tu_end", input_params={}
                )
            ])

        # Second turn: simple greeting, no tool calls.
        async def second_stream(*args, **kwargs):
            yield "Hello again. "

        # First turn — sets the flag.
        async def first_then_resume(*args, **kwargs):
            yield "Take care. "

        # Use an iterator over per-call generators.
        streams = iter([first_stream, first_then_resume, second_stream])

        async def dispatch(*args, **kwargs):
            gen = next(streams)(*args, **kwargs)
            async for tok in gen:
                yield tok

        agent._llm.stream_call = dispatch
        agent._async_gateway.execute = AsyncMock(
            side_effect=AssertionError("end_session must not be routed to Action Gateway")
        )

        # Turn 1 — terminates.
        events1 = await _collect_events(agent, _make_turn_input())
        done1 = [e for e in events1 if isinstance(e, DoneEvent)][0]
        assert done1.session_ended is True
        assert agent._manager_agent._session_ended_flag is True

        # Turn 2 — must NOT inherit the previous flag.
        agent._nlu_processor.process.return_value = NLUResult(
            intent="greeting", entities={}, sentiment="neutral", confidence=0.9
        )
        events2 = await _collect_events(agent, _make_turn_input())
        done2 = [e for e in events2 if isinstance(e, DoneEvent)][0]
        assert done2.session_ended is False
        assert agent._manager_agent._session_ended_flag is False

# ---------------------------------------------------------------------------
# #193: cross-turn tool_use/tool_result replay
# ---------------------------------------------------------------------------


class TestStreamTurnRecentToolExchanges:
    """Cover persist + replay of prior tool_use/tool_result pairs across turns."""

    @pytest.mark.asyncio
    async def test_tool_round_persisted_to_memory(self):
        """After a tool turn, ``recent_tool_exchanges`` is written to Memory Layer."""
        agent = _make_agent_core()

        call_count = 0

        async def mock_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield "Looking that up"
                raise ToolUseRequested([
                    ToolCall(
                        tool_name="onest_market_lookup",
                        tool_use_id="tu_t1",
                        input_params={"trade": "welder"},
                    )
                ])
            else:
                yield "Here are the results. "

        agent._llm.stream_call = mock_stream
        agent._async_gateway.execute.return_value = ToolResult(
            tool_use_id="tu_t1",
            tool_name="onest_market_lookup",
            result={"jobs": []},
            success=True,
            result_text='{"jobs":[{"title":"Welder","wage":500}]}',
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="search", entities={}, sentiment="neutral", confidence=0.9
        )

        await _collect_events(agent, _make_turn_input())

        # Wait for fire-and-forget memory writes spawned via create_task
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        write_calls = agent._async_memory.write.await_args_list
        rte_calls = [c for c in write_calls if c.args[3] == "recent_tool_exchanges"]
        assert rte_calls, "Expected a write to recent_tool_exchanges"
        stored = rte_calls[-1].args[4]
        assert isinstance(stored, list) and len(stored) == 1
        ex = stored[0]
        assert ex["tool_uses"][0]["name"] == "onest_market_lookup"
        assert ex["tool_uses"][0]["input"] == {"trade": "welder"}
        assert ex["tool_results"][0]["tool_use_id"] == "tu_t1"
        assert "Welder" in ex["tool_results"][0]["content"]

    @pytest.mark.asyncio
    async def test_prior_exchanges_replayed_into_messages(self):
        """T2's stream_call receives prior tool_use/tool_result pairs in messages."""
        agent = _make_agent_core()
        prior_exchange = {
            "tool_uses": [
                {
                    "type": "tool_use",
                    "id": "tu_prev",
                    "name": "onest_market_lookup",
                    "input": {"trade": "welder"},
                }
            ],
            "tool_results": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_prev",
                    "content": '{"jobs":[{"title":"Welder"}]}',
                }
            ],
        }
        agent._async_memory.context_bundle.return_value = ContextBundle(
            session={
                "current_subagent_id": "start",
                "recent_tool_exchanges": [prior_exchange],
            },
            profile={},
        )

        captured_messages: list = []

        async def mock_stream(*args, **kwargs):
            captured_messages.append(kwargs.get("messages") or args[0])
            yield "Reusing prior data. "

        agent._llm.stream_call = mock_stream
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="follow_up", entities={}, sentiment="neutral", confidence=0.9
        )

        await _collect_events(agent, _make_turn_input(user_message="What was the wage?"))

        assert captured_messages, "stream_call should have been invoked"
        msgs = captured_messages[0]
        # First two messages should be the replayed assistant tool_use + user tool_result.
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"][0]["type"] == "tool_use"
        assert msgs[0]["content"][0]["name"] == "onest_market_lookup"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"][0]["type"] == "tool_result"
        assert msgs[1]["content"][0]["tool_use_id"] == "tu_prev"
        # Tool gateway must NOT have been invoked again for the same params.
        assert agent._async_gateway.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_max_items_cap_drops_oldest(self):
        """When more than max_items exchanges accumulate, the oldest is dropped."""
        agent = _make_agent_core()
        # Configure cap of 3 (default) and seed 3 prior exchanges.
        prior = [
            {
                "tool_uses": [
                    {"type": "tool_use", "id": f"tu_{i}", "name": "lookup", "input": {"i": i}}
                ],
                "tool_results": [
                    {"type": "tool_result", "tool_use_id": f"tu_{i}", "content": f"r{i}"}
                ],
            }
            for i in range(3)
        ]
        agent._async_memory.context_bundle.return_value = ContextBundle(
            session={
                "current_subagent_id": "start",
                "recent_tool_exchanges": list(prior),
            },
            profile={},
        )

        call_count = 0

        async def mock_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield "ok"
                raise ToolUseRequested([
                    ToolCall(tool_name="lookup", tool_use_id="tu_new", input_params={"i": 99})
                ])
            else:
                yield "Done. "

        agent._llm.stream_call = mock_stream
        agent._async_gateway.execute.return_value = ToolResult(
            tool_use_id="tu_new",
            tool_name="lookup",
            result={"x": 1},
            success=True,
            result_text="r99",
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="search", entities={}, sentiment="neutral", confidence=0.9
        )

        await _collect_events(agent, _make_turn_input())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        rte_calls = [
            c for c in agent._async_memory.write.await_args_list
            if c.args[3] == "recent_tool_exchanges"
        ]
        assert rte_calls, "Expected a write to recent_tool_exchanges"
        stored = rte_calls[-1].args[4]
        # Cap is 3 → oldest (i=0) must be dropped, newest (tu_new) must be present.
        assert len(stored) == 3
        ids = [ex["tool_uses"][0]["id"] for ex in stored]
        assert "tu_0" not in ids
        assert ids[-1] == "tu_new"

    @pytest.mark.asyncio
    async def test_max_items_zero_disables_replay_and_persist(self):
        """When max_items=0, no replay and no persist happens."""
        agent = _make_agent_core()
        agent._config["agent"]["recent_tool_exchanges"] = {"max_items": 0, "max_chars": 4000}

        prior = [
            {
                "tool_uses": [
                    {"type": "tool_use", "id": "tu_x", "name": "lookup", "input": {}}
                ],
                "tool_results": [
                    {"type": "tool_result", "tool_use_id": "tu_x", "content": "r"}
                ],
            }
        ]
        agent._async_memory.context_bundle.return_value = ContextBundle(
            session={
                "current_subagent_id": "start",
                "recent_tool_exchanges": list(prior),
            },
            profile={},
        )

        captured_messages: list = []

        async def mock_stream(*args, **kwargs):
            captured_messages.append(kwargs.get("messages") or args[0])
            yield "Hi. "

        agent._llm.stream_call = mock_stream
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._nlu_processor = MagicMock()
        agent._nlu_processor.process.return_value = NLUResult(
            intent="greeting", entities={}, sentiment="neutral", confidence=0.9
        )

        await _collect_events(agent, _make_turn_input())

        assert captured_messages
        # No replayed messages — first message should be the user turn directly.
        assert captured_messages[0][0]["role"] == "user"
        assert all(
            isinstance(c, str) or c.get("type") != "tool_use"
            for c in (captured_messages[0][0].get("content") or [])
            if isinstance(c, dict)
        )


class TestRecentToolExchangesHelpers:
    """Pure-function tests for the cross-turn replay helpers."""

    def test_build_messages_skips_malformed(self):
        agent = _make_agent_core()
        msgs = agent._build_tool_exchange_messages([
            {},
            {"tool_uses": [], "tool_results": []},
            None,  # type: ignore[list-item]
            {
                "tool_uses": [{"type": "tool_use", "id": "a", "name": "t", "input": {}}],
                "tool_results": [{"type": "tool_result", "tool_use_id": "a", "content": "x"}],
            },
        ])
        assert len(msgs) == 2
        assert msgs[0]["role"] == "assistant"
        assert msgs[1]["role"] == "user"

    def test_truncate_tool_result_content(self):
        agent = _make_agent_core()
        assert agent._truncate_tool_result_content("hello", 0) == "hello"
        assert agent._truncate_tool_result_content("hello", 100) == "hello"
        assert agent._truncate_tool_result_content("abcdef", 3) == "abc"
        assert agent._truncate_tool_result_content("", 10) == ""

    def test_capture_tool_exchange_truncates(self):
        agent = _make_agent_core()
        tc = ToolCall(tool_name="lookup", tool_use_id="tu_1", input_params={"q": "x"})
        results = [{"type": "tool_result", "tool_use_id": "tu_1", "content": "abcdefghij"}]
        ex = agent._capture_tool_exchange([tc], results, max_chars=4)
        assert ex is not None
        assert ex["tool_results"][0]["content"] == "abcd"
        assert ex["tool_uses"][0]["name"] == "lookup"

    def test_capture_tool_exchange_empty_returns_none(self):
        agent = _make_agent_core()
        assert agent._capture_tool_exchange([], [], 100) is None
