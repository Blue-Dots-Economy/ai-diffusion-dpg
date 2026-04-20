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
