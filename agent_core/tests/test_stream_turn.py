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
from src.chat_provider.base import ChatProviderBase
from src.chat_provider.base import ToolUseRequested as ChatToolUseRequested
from src.chat_provider.types import (
    ChatResponse,
    Message,
    SystemPrompt,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)
from tests.fakes import fake_understander


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
            "nlu_processor": {},
        },
        "entity_persistence": {"scope": "persistent"},
        "entity_to_profile_field": {},
    }

    llm = MagicMock(spec=ChatProviderBase)
    llm.get_active_model.return_value = "test-model"

    memory = MagicMock()
    trust = MagicMock()
    ke = MagicMock()
    tool_registry = MagicMock()
    tool_registry.get_route.return_value = None
    manager_agent = MagicMock()
    manager_agent.build_system_prompt.return_value = SystemPrompt(blocks=[TextBlock(text="System prompt")])
    manager_agent.build_messages.return_value = [Message(role="user", content=[TextBlock(text="Hello")])]
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
        chat_provider=llm,
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
        nlu_chat_provider=_nlu_provider_mock(),
    )
    defaults.update(overrides)
    agent = AgentCore(**defaults)
    agent._understander = fake_understander()
    return agent


def _nlu_provider_mock() -> MagicMock:
    """A dedicated-NLU provider stand-in (never called once the understander is faked)."""
    p = MagicMock()
    p.capabilities.supports_prompt_cache = False
    return p


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

        agent._llm.stream = mock_stream
        # Patch NLU to return a simple result
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("Hello", "english")
        agent._understander = fake_understander(NLUResult(
            intent="greeting", entities={}, confidence=0.9
        ))

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
        agent._understander = fake_understander(NLUResult(
            intent="unknown", entities={}, confidence=0.5
        ))

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
        agent._understander = fake_understander(NLUResult(
            intent="unknown", entities={}, confidence=0.5
        ))

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
                raise ChatToolUseRequested([
                    ToolUseBlock(tool_name="search", tool_use_id="tu_1", input={"q": "test"})
                ])
            else:
                yield "Here's what I found. "

        agent._llm.stream = mock_stream
        agent._async_gateway.execute.return_value = ToolResult(
            tool_use_id="tu_1", tool_name="search",
            result={"answer": "42"}, success=True, result_text="42"
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._understander = fake_understander(NLUResult(
            intent="search", entities={}, confidence=0.9
        ))

        events = await _collect_events(agent, _make_turn_input())

        signal_events = [e for e in events if isinstance(e, SignalEvent)]
        tool_start = [e for e in signal_events if e.stage == "tool_start"]
        tool_end = [e for e in signal_events if e.stage == "tool_end"]
        done_events = [e for e in events if isinstance(e, DoneEvent)]

        assert len(tool_start) == 1
        assert len(tool_end) == 1
        assert done_events[0].was_tool_used is True

    @pytest.mark.asyncio
    async def test_tool_start_names_the_tools_being_run(self):
        """Channels turn tool_start into caller-facing status ("looking up
        jobs"), so the signal must say which tool — not just that one runs."""
        agent = _make_agent_core()
        call_count = 0

        async def mock_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ChatToolUseRequested([
                    ToolUseBlock(tool_name="fetch_jobs", tool_use_id="tu_1", input={}),
                ])
            if call_count == 2:
                raise ChatToolUseRequested([
                    ToolUseBlock(tool_name="apply_job", tool_use_id="tu_2", input={}),
                ])
            yield "Done. "

        agent._llm.stream = mock_stream
        agent._async_gateway.execute.return_value = ToolResult(
            tool_use_id="tu_1", tool_name="fetch_jobs",
            result={}, success=True, result_text="ok"
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._understander = fake_understander(NLUResult(
            intent="search", entities={}, confidence=0.9
        ))

        events = await _collect_events(agent, _make_turn_input())

        tool_start = [e for e in events
                      if isinstance(e, SignalEvent) and e.stage == "tool_start"]
        assert [e.tools for e in tool_start] == [["fetch_jobs"], ["apply_job"]]
        assert '"tools": ["fetch_jobs"]' in tool_start[0].to_sse()

    def test_signal_event_tools_default_empty(self):
        """Every other stage leaves tools empty, so existing consumers that
        ignore the field see no change."""
        assert SignalEvent(stage="nlu", status="start").tools == []


class TestStreamTurnTrustOutput:

    @pytest.mark.asyncio
    async def test_trust_output_blocks_sentence(self):
        """Trust output block replaces sentence with fallback."""
        agent = _make_agent_core()

        async def mock_stream(*args, **kwargs):
            yield "Bad content here. "

        agent._llm.stream = mock_stream
        agent._async_trust.check_output.return_value = TrustCheckResult(
            passed=False, action="block", reason="unsafe"
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._understander = fake_understander(NLUResult(
            intent="greeting", entities={}, confidence=0.9
        ))

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

        agent._llm.stream = mock_stream
        agent._async_trust.check_output.side_effect = Exception("Connection refused")
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._understander = fake_understander(NLUResult(
            intent="greeting", entities={}, confidence=0.9
        ))

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

        agent._llm.stream = mock_stream
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("Hello", "english")
        agent._understander = fake_understander(NLUResult(
            intent="greeting", entities={}, confidence=0.9
        ))

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
        agent._understander = fake_understander(NLUResult(
            intent="termination_intent", entities={}, confidence=0.95
        ))
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
                raise ChatToolUseRequested([
                    ToolUseBlock(
                        tool_name="end_session",
                        tool_use_id="tu_end",
                        input={"reason": "user_said_bye"},
                    )
                ])
            else:
                yield "Take care. "

        agent._llm.stream = mock_stream

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
    async def test_end_session_alone_skips_the_second_llm_call(self):
        """GH-204: end_session resolves internally and its description asks the
        model to speak the closing line alongside it, so a second pass would only
        regenerate a goodbye that already exists. It must not be made."""
        agent = self._make_end_session_agent()

        call_count = 0

        async def mock_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield "Goodbye, take care."
                raise ChatToolUseRequested([
                    ToolUseBlock(
                        tool_name="end_session",
                        tool_use_id="tu_end",
                        input={"reason": "user_goodbye"},
                    )
                ])
            raise AssertionError(
                "LLM was called a second time for an end_session-only round"
            )

        agent._llm.stream = mock_stream
        agent._async_gateway.execute = AsyncMock(
            side_effect=AssertionError("end_session must not reach Action Gateway")
        )

        events = await _collect_events(agent, _make_turn_input())

        assert call_count == 1, "the second LLM pass must be skipped"
        # The call still ends, and the caller still hears the goodbye.
        done = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done) == 1
        assert done[0].session_ended is True
        spoken = " ".join(
            e.text for e in events if isinstance(e, SentenceEvent)
        )
        assert "Goodbye" in spoken, "the closing line must still reach the caller"

    @pytest.mark.asyncio
    async def test_end_session_without_text_still_makes_the_second_call(self):
        """The skip is guarded on text existing. When the model called
        end_session and said nothing, the second pass is what writes the reply —
        skipping it would hang up on the caller in silence."""
        agent = self._make_end_session_agent()

        call_count = 0

        async def mock_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ChatToolUseRequested([
                    ToolUseBlock(
                        tool_name="end_session",
                        tool_use_id="tu_end",
                        input={"reason": "user_goodbye"},
                    )
                ])
                yield  # pragma: no cover - generator marker
            else:
                yield "Thank you for calling."

        agent._llm.stream = mock_stream
        agent._async_gateway.execute = AsyncMock(
            side_effect=AssertionError("end_session must not reach Action Gateway")
        )

        events = await _collect_events(agent, _make_turn_input())

        assert call_count == 2, "with no text produced, the second pass must run"
        spoken = " ".join(
            e.text for e in events if isinstance(e, SentenceEvent)
        )
        assert "Thank you" in spoken

    @pytest.mark.asyncio
    async def test_end_session_alongside_another_tool_still_makes_the_second_call(self):
        """A real tool in the same round returns a result the model has not seen.
        Only an end_session-ONLY round may skip the pass."""
        agent = self._make_end_session_agent()

        call_count = 0

        async def mock_stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield "Let me check."
                raise ChatToolUseRequested([
                    ToolUseBlock(
                        tool_name="fetch_jobs",
                        tool_use_id="tu_jobs",
                        input={"query_text": "welder jobs"},
                    ),
                    ToolUseBlock(
                        tool_name="end_session",
                        tool_use_id="tu_end",
                        input={"reason": "task_complete"},
                    ),
                ])
            else:
                yield "Here is what I found."

        agent._llm.stream = mock_stream
        agent._async_gateway.execute = AsyncMock(
            return_value=ToolResult(
                tool_use_id="tu_jobs",
                tool_name="fetch_jobs",
                result={"items": []},
                success=True,
                result_text="no jobs",
            )
        )

        events = await _collect_events(agent, _make_turn_input())

        assert call_count == 2, (
            "a round carrying a real tool result must still run the second pass"
        )

    @pytest.mark.asyncio
    async def test_session_ended_flag_cleared_between_turns(self):
        """The end_session flag must not leak from one turn into the next."""
        agent = self._make_end_session_agent()

        async def first_stream(*args, **kwargs):
            yield "Bye"
            raise ChatToolUseRequested([
                ToolUseBlock(
                    tool_name="end_session", tool_use_id="tu_end", input={}
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

        agent._llm.stream = dispatch
        agent._async_gateway.execute = AsyncMock(
            side_effect=AssertionError("end_session must not be routed to Action Gateway")
        )

        # Turn 1 — terminates.
        events1 = await _collect_events(agent, _make_turn_input())
        done1 = [e for e in events1 if isinstance(e, DoneEvent)][0]
        assert done1.session_ended is True
        assert agent._manager_agent._session_ended_flag is True

        # Turn 2 — must NOT inherit the previous flag.
        agent._understander = fake_understander(NLUResult(
            intent="greeting", entities={}, confidence=0.9
        ))
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
                raise ChatToolUseRequested([
                    ToolUseBlock(
                        tool_name="onest_market_lookup",
                        tool_use_id="tu_t1",
                        input={"trade": "welder"},
                    )
                ])
            else:
                yield "Here are the results. "

        agent._llm.stream = mock_stream
        agent._async_gateway.execute.return_value = ToolResult(
            tool_use_id="tu_t1",
            tool_name="onest_market_lookup",
            result={"jobs": []},
            success=True,
            result_text='{"jobs":[{"title":"Welder","wage":500}]}',
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._understander = fake_understander(NLUResult(
            intent="search", entities={}, confidence=0.9
        ))

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

        captured_requests: list = []

        async def mock_stream(request, *, abort_event=None):
            captured_requests.append(request)
            yield "Reusing prior data. "

        agent._llm.stream = mock_stream
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._understander = fake_understander(NLUResult(
            intent="follow_up", entities={}, confidence=0.9
        ))

        await _collect_events(agent, _make_turn_input(user_message="What was the wage?"))

        assert captured_requests, "stream should have been invoked"
        msgs = captured_requests[0].messages
        # First two messages should be the replayed assistant tool_use + user tool_result.
        assert msgs[0].role == "assistant"
        assert msgs[0].content[0].type == "tool_use"
        assert msgs[0].content[0].tool_name == "onest_market_lookup"
        assert msgs[1].role == "user"
        assert msgs[1].content[0].type == "tool_result"
        assert msgs[1].content[0].tool_use_id == "tu_prev"
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
                raise ChatToolUseRequested([
                    ToolUseBlock(tool_name="lookup", tool_use_id="tu_new", input={"i": 99})
                ])
            else:
                yield "Done. "

        agent._llm.stream = mock_stream
        agent._async_gateway.execute.return_value = ToolResult(
            tool_use_id="tu_new",
            tool_name="lookup",
            result={"x": 1},
            success=True,
            result_text="r99",
        )
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._understander = fake_understander(NLUResult(
            intent="search", entities={}, confidence=0.9
        ))

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

        captured_requests: list = []

        async def mock_stream(request, *, abort_event=None):
            captured_requests.append(request)
            yield "Hi. "

        agent._llm.stream = mock_stream
        agent._language_normaliser = MagicMock()
        agent._language_normaliser.normalise.return_value = ("msg", "english")
        agent._understander = fake_understander(NLUResult(
            intent="greeting", entities={}, confidence=0.9
        ))

        await _collect_events(agent, _make_turn_input())

        assert captured_requests, "stream should have been invoked"
        # No replayed messages — first message should be the user turn directly.
        first_msg = captured_requests[0].messages[0]
        assert first_msg.role == "user"
        # No tool_use blocks in the content (no replay when max_items=0)
        assert all(
            not hasattr(block, "tool_name")
            for block in first_msg.content
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
        assert msgs[0].role == "assistant"
        assert msgs[1].role == "user"

    def test_build_messages_skips_listed_tools_pairwise(self):
        agent = _make_agent_core()
        ex = [{"tool_uses": [{"type": "tool_use", "id": "a", "name": "fetch_profile", "input": {}},
                             {"type": "tool_use", "id": "b", "name": "fetch_jobs", "input": {}}],
               "tool_results": [{"type": "tool_result", "tool_use_id": "a", "content": "P"},
                                {"type": "tool_result", "tool_use_id": "b", "content": "J"}]},
              {"tool_uses": [{"type": "tool_use", "id": "c", "name": "fetch_profile", "input": {}}],
               "tool_results": [{"type": "tool_result", "tool_use_id": "c", "content": "P2"}]}]
        msgs = agent._build_tool_exchange_messages(ex, skip_tools={"fetch_profile"})
        assert len(msgs) == 2                                  # second exchange dropped entirely
        assert [b.tool_use_id for b in msgs[1].content] == ["b"]

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


# ---------------------------------------------------------------------------
# Tool-result persistence (stream path)
# ---------------------------------------------------------------------------

_TR_CONFIG = {
    "connectors": {
        "read": [
            {"name": "get_balance", "cache": {"scope": "session", "ttl_seconds": 600}},
            {"name": "fetch_profile", "cache": {"scope": "user", "ttl_seconds": 600}},
        ],
        "write": [{"name": "save_profile", "invalidates": ["fetch_profile"]}],
    },
    "memory_tool": {"name": "remember", "fields": {
        "account": {"scope": "session", "grounded_in": ["get_balance"]}}},
}


def _tr_entry():
    """A fresh stored get_balance result for account 12345."""
    import time as _time
    from src.tool_results import args_hash
    return {"tool": "get_balance", "args_hash": args_hash({"account": "12345"}),
            "data": {"account": "12345", "balance": 100}, "fetched_at": _time.time(),
            "expires_at": 9e12, "origin": "turn", "scope": "session"}


def _tr_agent(rounds, entries=None, gateway_text='{"balance": 5}', remember=False):
    """AgentCore whose LLM requests each round in ``rounds`` in turn, then answers.

    Returns ``(agent, order, requests)``: ``order`` records LLM calls
    (``llm1``, ``llm2`` …) and ``apply`` persistence calls in the order they
    happened; ``requests`` holds every stream request.
    """
    from src.remember import RememberTool
    from src.tool_results import ToolResultPolicies

    agent = _make_agent_core()
    agent._tool_policies = ToolResultPolicies.from_config(_TR_CONFIG)
    agent._remember = RememberTool.from_config(_TR_CONFIG) if remember else None
    agent._async_memory.context_bundle.return_value = ContextBundle(
        session={"current_subagent_id": "start"}, profile={},
        tool_results=list(entries or []),
    )
    order: list[str] = []
    requests: list = []
    calls = {"n": 0}

    async def mock_stream(request, *, abort_event=None):
        calls["n"] += 1
        requests.append(request)
        order.append(f"llm{calls['n']}")
        if calls["n"] <= len(rounds):
            yield "Checking. "
            raise ChatToolUseRequested(rounds[calls["n"] - 1])
        yield "Done. "

    async def _apply(*args, **kwargs):
        order.append("apply")

    agent._llm.stream = mock_stream
    agent._async_memory.apply_tool_results = AsyncMock(side_effect=_apply)
    agent._async_memory.write_strict = AsyncMock(return_value=(True, ""))
    agent._async_gateway.execute = AsyncMock(side_effect=lambda tc, *a, **k: ToolResult(
        tool_use_id=tc.tool_use_id, tool_name=tc.tool_name, result={}, success=True,
        result_text=gateway_text, projected=True,
    ))
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("msg", "english")
    agent._understander = fake_understander(NLUResult(
        intent="search", entities={}, confidence=0.9
    ))
    return agent, order, requests


def _last_tool_result_texts(request) -> list[str]:
    """Tool-result contents of the final user message of a stream request."""
    return [b.content for b in request.messages[-1].content if b.type == "tool_result"]


class TestStreamTurnToolResultPersistence:

    async def test_stream_cache_hit_skips_gateway(self):
        agent, _order, requests = _tr_agent(
            [[ToolUseBlock(tool_name="get_balance", tool_use_id="tu_1",
                           input={"account": "12345"})]],
            entries=[_tr_entry()],
        )
        await _collect_events(agent, _make_turn_input())
        agent._async_gateway.execute.assert_not_awaited()
        assert _last_tool_result_texts(requests[1])[0].startswith("(stored result")
        agent._async_memory.apply_tool_results.assert_not_awaited()

    async def test_stream_live_call_persists_immediately(self):
        agent, order, _requests = _tr_agent(
            [[ToolUseBlock(tool_name="get_balance", tool_use_id="tu_1",
                           input={"account": "999", "force_refresh": True})]],
        )
        await _collect_events(agent, _make_turn_input())
        agent._async_memory.apply_tool_results.assert_awaited_once()
        sid, uid, batch = agent._async_memory.apply_tool_results.await_args.args
        assert (sid, uid) == ("sess-1", "user-1")
        assert batch["invalidate"] == [] and len(batch["puts"]) == 1
        assert batch["puts"][0]["data"] == {"balance": 5}
        assert order.index("apply") < order.index("llm2")
        # force_refresh is framework-only and never reaches the connector.
        sent = agent._async_gateway.execute.await_args.args[0]
        assert "force_refresh" not in sent.input_params

    async def test_stream_write_invalidation_survives_interruption(self):
        from src.models import TurnRecord
        from tests.test_stream_turn_lifecycle import _run

        agent, _order, _requests = _tr_agent([
            [ToolUseBlock(tool_name="save_profile", tool_use_id="tu_1", input={"name": "A"})],
            [ToolUseBlock(tool_name="search", tool_use_id="tu_2", input={})],
        ])
        record = TurnRecord()
        events = await _run(agent, record, abort_after_tool_end=1)
        assert not any(isinstance(e, DoneEvent) for e in events)   # really interrupted
        agent._async_memory.apply_tool_results.assert_awaited_once_with(
            "sess-1", "user-1", {"invalidate": ["fetch_profile"], "puts": []},
        )

    @pytest.mark.parametrize("nested", [False, True])
    async def test_stream_remember_routed_locally(self, nested):
        remember = [ToolUseBlock(tool_name="remember", tool_use_id="tu_r",
                                 input={"field": "account", "value": "12345"})]
        rounds = ([[ToolUseBlock(tool_name="search", tool_use_id="tu_s", input={})], remember]
                  if nested else [remember])
        agent, _order, requests = _tr_agent(rounds, entries=[_tr_entry()], remember=True)
        await _collect_events(agent, _make_turn_input())
        agent._async_memory.write_strict.assert_awaited_once_with(
            "sess-1", "user-1", "session", "account", "12345",
        )
        called = [c.args[0].tool_name for c in agent._async_gateway.execute.await_args_list]
        assert "remember" not in called
        assert _last_tool_result_texts(requests[-1]) == ["Saved account."]

    async def test_stream_nested_round_uses_cache(self):
        agent, _order, requests = _tr_agent(
            [[ToolUseBlock(tool_name="search", tool_use_id="tu_1", input={})],
             [ToolUseBlock(tool_name="get_balance", tool_use_id="tu_2",
                           input={"account": "12345"})]],
            entries=[_tr_entry()],
        )
        await _collect_events(agent, _make_turn_input())
        called = [c.args[0].tool_name for c in agent._async_gateway.execute.await_args_list]
        assert called == ["search"]
        assert _last_tool_result_texts(requests[2])[0].startswith("(stored result")

    async def test_stream_prompt_gets_known_facts_and_augmented_tools(self):
        agent, _order, requests = _tr_agent([], entries=[_tr_entry()], remember=True)
        agent._workflow.resolve_tools_for.return_value = [
            {"name": "get_balance", "input_schema": {"type": "object", "properties": {}}}]
        await _collect_events(agent, _make_turn_input())
        kwargs = agent._manager_agent.build_system_prompt.call_args.kwargs
        assert "get_balance" in kwargs["known_facts"]
        tools = {t.name: t for t in requests[0].tools}
        assert set(tools) == {"get_balance", "remember"}
        assert "force_refresh" in tools["get_balance"].input_schema["properties"]

    async def test_stream_prompt_session_fields_reach_build_system_prompt(self):
        agent, _order, _requests = _tr_agent([], entries=[_tr_entry()])
        agent._prompt_session_fields = ["profile_item_id"]
        agent._async_memory.context_bundle.return_value.session["profile_item_id"] = "p1"
        await _collect_events(agent, _make_turn_input())
        profile = agent._manager_agent.build_system_prompt.call_args.kwargs["profile"]
        assert profile["profile_item_id"] == "p1"

    async def test_stream_replay_skips_tools_with_fresh_stored_results(self):
        prior = {"tool_uses": [{"type": "tool_use", "id": "tu_p", "name": "get_balance",
                                "input": {"account": "12345"}}],
                 "tool_results": [{"type": "tool_result", "tool_use_id": "tu_p",
                                   "content": '{"balance": 1}'}]}
        agent, _order, requests = _tr_agent([], entries=[_tr_entry()])
        agent._async_memory.context_bundle.return_value.session["recent_tool_exchanges"] = [prior]
        await _collect_events(agent, _make_turn_input())
        assert all(b.type != "tool_use" for m in requests[0].messages for b in m.content)

    async def test_stream_persist_failure_is_logged_and_turn_completes(self, caplog):
        agent, _order, _requests = _tr_agent(
            [[ToolUseBlock(tool_name="get_balance", tool_use_id="tu_1",
                           input={"account": "999"})]],
        )
        agent._async_memory.apply_tool_results = AsyncMock(side_effect=RuntimeError("down"))
        events = await _collect_events(agent, _make_turn_input())
        assert isinstance(events[-1], DoneEvent) and events[-1].turn_status == "completed"
        errs = [r for r in caplog.records if r.message == "orchestrator.apply_tool_results_error"]
        assert errs and errs[0].error == "RuntimeError"      # class only, never the message


# ---------------------------------------------------------------------------
# Session bootstrap wiring (session-bootstrap spec §5) — stream path
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402

from src.session_bootstrap import LATCH, SessionBootstrap  # noqa: E402

_BOOT_CONFIG = {
    "connectors": {"read": [{"name": "fetch_profile", "cache": {"scope": "session", "ttl_seconds": 1800}}]},
    "session_bootstrap": {"timeout_ms": 1500, "steps": [{"type": "tool", "tool": "fetch_profile"}]},
}


def _boot_result(success=True, **session_values):
    return ToolResult(tool_use_id="bootstrap-0", tool_name="fetch_profile", result={}, success=success,
                      result_text=_json.dumps({"items": [{"item_id": "p1"}]}), projected=True,
                      session_values=session_values, error=None if success else "boom")


def _boot_stream_agent(result=None, side_effect=None, **overrides):
    """_make_agent_core with a configured bootstrap; returns (agent, order)."""
    from src.tool_results import ToolResultPolicies

    agent = _make_agent_core(**overrides)
    agent._tool_policies = ToolResultPolicies.from_config(_BOOT_CONFIG)
    agent._bootstrap = SessionBootstrap.from_config(_BOOT_CONFIG, agent._tool_policies)
    order: list[str] = []

    async def _execute(tc, *a, **kw):
        order.append("bootstrap_execute")
        if side_effect is not None:
            raise side_effect
        return result or _boot_result(has_age=True)

    agent._async_gateway.execute = AsyncMock(side_effect=_execute)

    async def mock_stream(*args, **kwargs):
        order.append("llm")
        yield "Hello there. "

    agent._llm.stream = mock_stream
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("Hello", "english")
    agent._understander = fake_understander(NLUResult(
        intent="greeting", entities={}, confidence=0.9))
    return agent, order


class TestStreamSessionBootstrap:

    @pytest.mark.asyncio
    async def test_stream_bootstrap_runs_once_before_llm_and_writes_latch(self):
        agent, order = _boot_stream_agent()
        events = await _collect_events(agent, _make_turn_input())
        assert isinstance(events[-1], DoneEvent)

        ex = agent._async_gateway.execute
        assert ex.await_count == 1
        tc = ex.await_args.args[0]
        assert isinstance(tc, ToolCall) and tc.tool_name == "fetch_profile"
        assert ex.await_args.args[1:3] == ("sess-1", "user-1")
        assert "session_values" in ex.await_args.kwargs
        assert order.index("bootstrap_execute") < order.index("llm")
        keys = [c.args[3] for c in agent._async_memory.write.await_args_list]
        assert LATCH in keys and "has_age" in keys
        assert keys.index(LATCH) < keys.index("has_age")
        batches = [c.args[2] for c in agent._async_memory.apply_tool_results.await_args_list]
        assert any(p["tool"] == "fetch_profile" and p["origin"] == "bootstrap"
                   for b in batches for p in b["puts"])

        # Second turn: latch present on the (shared) bundle session → no re-run.
        assert agent._async_memory.context_bundle.return_value.session[LATCH] is True
        await _collect_events(agent, _make_turn_input())
        assert ex.await_count == 1

    @pytest.mark.asyncio
    async def test_stream_bootstrap_entry_reaches_known_facts(self):
        agent, _order = _boot_stream_agent()
        await _collect_events(agent, _make_turn_input())
        facts = agent._manager_agent.build_system_prompt.call_args.kwargs["known_facts"]
        assert "fetch_profile" in facts and "p1" in facts

    @pytest.mark.asyncio
    async def test_stream_no_bootstrap_when_not_configured(self):
        agent, order = _boot_stream_agent()
        agent._bootstrap = None
        await _collect_events(agent, _make_turn_input())
        agent._async_gateway.execute.assert_not_awaited()
        assert "bootstrap_execute" not in order

    @pytest.mark.asyncio
    async def test_stream_bootstrap_exception_never_breaks_the_turn(self):
        agent, _order = _boot_stream_agent(side_effect=RuntimeError("upstream down"))
        events = await _collect_events(agent, _make_turn_input())
        assert isinstance(events[-1], DoneEvent) and events[-1].turn_status == "completed"

    @pytest.mark.asyncio
    async def test_stream_bootstrap_run_error_is_contained(self):
        agent, _order = _boot_stream_agent()
        agent._bootstrap = MagicMock()
        agent._bootstrap.needed.return_value = True
        agent._bootstrap.run_async = AsyncMock(side_effect=RuntimeError("bug"))
        events = await _collect_events(agent, _make_turn_input())
        assert isinstance(events[-1], DoneEvent) and events[-1].turn_status == "completed"


class TestStreamSessionBootstrapOrdering:

    @pytest.mark.asyncio
    async def test_stream_memory_read_complete_and_step1_log_precede_bootstrap(self):
        """Memory-read latency/signal exclude the bootstrap (ruling R7)."""
        import logging
        from tests.test_orchestrator import _OrderHandler

        agent, order = _boot_stream_agent()
        log = logging.getLogger("src.orchestrator")
        handler, prev = _OrderHandler(order), log.level
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        try:
            events = []
            async for ev in agent.stream_turn(_make_turn_input()):
                if isinstance(ev, SignalEvent) and ev.stage == "memory_read" and ev.status == "complete":
                    order.append("memory_read_complete")
                events.append(ev)
        finally:
            log.removeHandler(handler)
            log.setLevel(prev)
        assert order.index("memory_read_complete") < order.index("bootstrap_execute")
        assert order.index("step1_logged") < order.index("bootstrap_execute") < order.index("llm")

    @pytest.mark.asyncio
    async def test_stream_bootstrap_skipped_without_gateway_logs_debug(self, caplog):
        import logging
        agent, _order = _boot_stream_agent()
        agent._async_gateway = None
        with caplog.at_level(logging.DEBUG, logger="src.orchestrator"):
            await agent._run_session_bootstrap_async(
                ContextBundle(session={}, profile={}), "sess-1", "user-1")
        recs = [r for r in caplog.records if r.message == "orchestrator.session_bootstrap_skipped"]
        assert len(recs) == 1 and recs[0].levelno == logging.DEBUG
        assert not hasattr(recs[0], "session_id")
