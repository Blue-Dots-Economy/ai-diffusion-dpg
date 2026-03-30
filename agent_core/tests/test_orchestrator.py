"""
agent_core/tests/test_orchestrator.py

Unit tests for AgentCore (orchestrator).
All 6 DPG interfaces, ManagerAgent, LanguageNormaliser, and NLUProcessor are mocked.

Coverage:
- Normal: full turn — both Trust checks called, TurnResult returned
- Normal: tool used — was_tool_used=True in result
- Normal: async post-turn runs (memory write + learning emit scheduled)
- Normal: KE retrieve() called with NLU-computed params
- Edge: empty user_message still processes without error
- Edge: empty messages from build_messages returns empty TurnResult
- Failure: Trust input returns "block" — blocked response returned, LLM not called
- Failure: Trust input returns "escalate" — escalated response, LLM not called
- Failure: Trust output returns "block" — response replaced with fallback message
- Failure: None turn_input raises ValueError
- Failure: empty session_id raises ValueError
- Early exit: unknown intent (+ low confidence) — KE and LLM not called
- HITL: loop_count >= threshold triggers escalation
- Termination: termination_intent triggers flush
"""

from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock, patch

from src.orchestrator import AgentCore
from src.models import (
    ContextBundle,
    LLMResponse,
    NLUResult,
    TrustCheckResult,
    TurnInput,
    TurnResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SESSION_ID = "sess_orch_001"
TIMESTAMP = int(time.time() * 1000)

VALID_CONFIG = {
    "conversation": {
        "unknown_intent_message": "I didn't understand that.",
        "blocked_message": "Blocked.",
        "escalation_message": "Escalating.",
        "output_blocked_message": "Output blocked.",
        "termination_message": "Goodbye.",
    },
    "preprocessing": {
        "nlu_processor": {
            "confidence_threshold": 0.5,
        }
    },
    "hitl": {"loop_count_threshold": 3},
    "workflow": {"termination_intents": ["termination_intent"]},
}

ALLOW = TrustCheckResult(passed=True, action="allow")
BLOCK = TrustCheckResult(passed=False, action="block", reason="harmful content")
ESCALATE = TrustCheckResult(passed=False, action="escalate", reason="escalation topic")

_DEFAULT_NLU = NLUResult(
    intent="market_truth_query",
    entities={"location": "Hubli"},
    sentiment="neutral",
    confidence=0.9,
)
_UNKNOWN_NLU = NLUResult(
    intent="unknown",
    entities={},
    sentiment="neutral",
    confidence=0.2,
)
_TERMINATION_NLU = NLUResult(
    intent="termination_intent",
    entities={},
    sentiment="neutral",
    confidence=0.95,
)


def _turn_input(message: str = "Hello") -> TurnInput:
    return TurnInput(
        session_id=SESSION_ID,
        user_message=message,
        channel="cli",
        timestamp_ms=TIMESTAMP,
    )


def _make_agent(
    trust_input: TrustCheckResult = ALLOW,
    trust_output: TrustCheckResult = ALLOW,
    llm_content: str = "LLM response.",
    manager_text: str = "Final response.",
    manager_tool_calls: list = None,
    prompt_messages: list = None,
    nlu_result: NLUResult = None,
    session_data: dict = None,
) -> AgentCore:
    """
    Build an AgentCore with all external dependencies mocked.

    LanguageNormaliser and NLUProcessor are replaced on the instance after
    construction so their LLM calls do not interfere with the primary LLM mock.
    """
    # Default current_node to "market_truth" so existing orchestrator tests exercise
    # the LLM path without being intercepted by the workflow gate.
    # Tests that specifically test the gate use test_workflow_gate.py.
    session = session_data if session_data is not None else {"current_node": "market_truth"}
    memory = MagicMock()
    memory.context_bundle.return_value = ContextBundle(
        session=session, profile={}, journey=None
    )

    trust = MagicMock()
    trust.check_input.return_value = trust_input
    trust.check_output.return_value = trust_output

    # KE returns a list of RetrievalChunk objects
    knowledge_engine = MagicMock()
    knowledge_engine.retrieve.return_value = []

    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content=llm_content,
        tool_calls=[],
        stop_reason="end_turn",
        model_used="claude-primary",
    )

    tool_registry = MagicMock()
    tool_registry.get_tool_definitions.return_value = []

    manager = MagicMock()
    manager.build_system_prompt.return_value = ""
    manager.build_messages.return_value = (
        prompt_messages if prompt_messages is not None
        else [{"role": "user", "content": "Hello"}]
    )
    manager.run_turn.return_value = (
        manager_text,
        manager_tool_calls or [],
    )

    learning = MagicMock()

    agent = AgentCore(
        config=VALID_CONFIG,
        llm_wrapper=llm,
        memory=memory,
        trust=trust,
        knowledge_engine=knowledge_engine,
        tool_registry=tool_registry,
        manager_agent=manager,
        learning=learning,
    )

    # Replace Language Normaliser and NLU Processor with controlled mocks
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("Hello", "english")

    agent._nlu_processor = MagicMock()
    agent._nlu_processor.process.return_value = nlu_result or _DEFAULT_NLU

    return agent


# ---------------------------------------------------------------------------
# Init validation
# ---------------------------------------------------------------------------

def test_raises_on_none_config():
    with pytest.raises(ValueError, match="config must not be None"):
        AgentCore(None, MagicMock(), MagicMock(), MagicMock(),
                  MagicMock(), MagicMock(), MagicMock(), MagicMock())


def test_raises_on_none_turn_input():
    agent = _make_agent()
    with pytest.raises(ValueError, match="turn_input must not be None"):
        agent.process_turn(None)


def test_raises_on_empty_session_id():
    agent = _make_agent()
    turn = TurnInput(session_id="", user_message="Hi", channel="cli", timestamp_ms=TIMESTAMP)
    with pytest.raises(ValueError, match="session_id must not be empty"):
        agent.process_turn(turn)


def test_raises_on_none_user_message():
    agent = _make_agent()
    turn = TurnInput(session_id=SESSION_ID, user_message=None, channel="cli", timestamp_ms=TIMESTAMP)
    with pytest.raises(ValueError, match="user_message must not be None"):
        agent.process_turn(turn)


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------

def test_process_turn_returns_turn_result():
    agent = _make_agent()
    result = agent.process_turn(_turn_input())
    assert isinstance(result, TurnResult)
    assert result.session_id == SESSION_ID
    assert result.response_text == "Final response."


def test_trust_check_input_called_exactly_once():
    agent = _make_agent()
    agent.process_turn(_turn_input())
    agent._trust.check_input.assert_called_once_with(SESSION_ID, "Hello")


def test_trust_check_output_called_exactly_once():
    agent = _make_agent()
    agent.process_turn(_turn_input())
    agent._trust.check_output.assert_called_once_with(SESSION_ID, "Final response.")


def test_both_trust_checks_always_called_on_successful_turn():
    agent = _make_agent()
    agent.process_turn(_turn_input())
    assert agent._trust.check_input.call_count == 1
    assert agent._trust.check_output.call_count == 1


def test_tool_used_flag_set_when_tool_calls_present():
    from src.models import ToolCall
    tc = ToolCall("get_data", "tu_1", {})
    agent = _make_agent(manager_tool_calls=[tc])
    result = agent.process_turn(_turn_input())
    assert result.was_tool_used is True


def test_tool_used_flag_false_when_no_tools():
    agent = _make_agent(manager_tool_calls=[])
    result = agent.process_turn(_turn_input())
    assert result.was_tool_used is False


def test_language_normaliser_called_with_raw_input():
    agent = _make_agent()
    agent.process_turn(_turn_input("kaam chahiye"))
    agent._language_normaliser.normalise.assert_called_once()
    call_args = agent._language_normaliser.normalise.call_args
    assert call_args[1].get("raw_input") == "kaam chahiye"


def test_nlu_processor_called_with_normalised_input():
    agent = _make_agent()
    agent._language_normaliser.normalise.return_value = ("kaam chahiye normalised", "hinglish")
    agent.process_turn(_turn_input("kaam chahiye"))
    call_args = agent._nlu_processor.process.call_args
    assert call_args[1].get("normalised_input") == "kaam chahiye normalised"


def test_ke_retrieve_called_with_nlu_params():
    """KE retrieve() must receive the NLU results computed by Agent Core."""
    agent = _make_agent(nlu_result=_DEFAULT_NLU)
    agent._language_normaliser.normalise.return_value = ("kaam chahiye", "hinglish")
    agent.process_turn(_turn_input("kaam chahiye"))
    call_kwargs = agent._knowledge_engine.retrieve.call_args[1]
    assert call_kwargs["intent"] == "market_truth_query"
    assert call_kwargs["detected_language"] == "hinglish"
    assert call_kwargs["normalised_input"] == "kaam chahiye"
    assert call_kwargs["entities"] == {"location": "Hubli"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_user_message_processes_without_error():
    agent = _make_agent()
    result = agent.process_turn(_turn_input(message=""))
    assert isinstance(result, TurnResult)


def test_empty_messages_from_build_messages_returns_empty_response():
    """When build_messages returns [], orchestrator returns empty response without LLM call."""
    agent = _make_agent(prompt_messages=[])
    result = agent.process_turn(_turn_input())
    assert result.response_text == ""
    agent._llm.call.assert_not_called()


# ---------------------------------------------------------------------------
# Early exit — unknown intent + low confidence
# ---------------------------------------------------------------------------

def test_unknown_intent_returns_early_without_ke_call():
    agent = _make_agent(nlu_result=_UNKNOWN_NLU)
    result = agent.process_turn(_turn_input())
    agent._knowledge_engine.retrieve.assert_not_called()
    agent._llm.call.assert_not_called()
    assert isinstance(result, TurnResult)


def test_unknown_intent_response_text_from_config():
    agent = _make_agent(nlu_result=_UNKNOWN_NLU)
    result = agent.process_turn(_turn_input())
    assert result.response_text == "I didn't understand that."


def test_low_confidence_with_unknown_intent_returns_early():
    """Early exit only triggers when BOTH intent=unknown AND confidence<threshold."""
    low_unknown = NLUResult(intent="unknown", entities={}, sentiment="neutral", confidence=0.3)
    agent = _make_agent(nlu_result=low_unknown)
    result = agent.process_turn(_turn_input())
    agent._knowledge_engine.retrieve.assert_not_called()
    assert isinstance(result, TurnResult)


def test_low_confidence_with_valid_intent_proceeds_to_ke():
    """Low confidence alone does NOT trigger early exit if intent is known."""
    low_valid = NLUResult(intent="market_truth_query", entities={}, sentiment="neutral", confidence=0.3)
    agent = _make_agent(nlu_result=low_valid)
    agent.process_turn(_turn_input())
    agent._knowledge_engine.retrieve.assert_called_once()


# ---------------------------------------------------------------------------
# Trust input failures
# ---------------------------------------------------------------------------

def test_blocked_input_returns_blocked_message():
    agent = _make_agent(trust_input=BLOCK)
    result = agent.process_turn(_turn_input())
    assert result.response_text == "Blocked."
    agent._llm.call.assert_not_called()


def test_blocked_input_does_not_call_knowledge_engine():
    agent = _make_agent(trust_input=BLOCK)
    agent.process_turn(_turn_input())
    agent._knowledge_engine.retrieve.assert_not_called()


def test_escalated_input_returns_escalation_message():
    agent = _make_agent(trust_input=ESCALATE)
    result = agent.process_turn(_turn_input())
    assert result.response_text == "Escalating."
    assert result.was_escalated is True


def test_escalated_input_does_not_call_llm():
    agent = _make_agent(trust_input=ESCALATE)
    agent.process_turn(_turn_input())
    agent._llm.call.assert_not_called()


# ---------------------------------------------------------------------------
# HITL bypass
# ---------------------------------------------------------------------------

def test_hitl_loop_count_triggers_escalation():
    """loop_count >= hitl_threshold escalates before LLM call."""
    agent = _make_agent(session_data={"loop_count": 3})
    result = agent.process_turn(_turn_input())
    assert result.was_escalated is True
    agent._llm.call.assert_not_called()


def test_hitl_below_threshold_proceeds_normally():
    agent = _make_agent(session_data={"loop_count": 2, "current_node": "market_truth"})
    result = agent.process_turn(_turn_input())
    assert result.was_escalated is False
    agent._llm.call.assert_called_once()


# ---------------------------------------------------------------------------
# Termination intent
# ---------------------------------------------------------------------------

def test_termination_intent_returns_goodbye_message():
    agent = _make_agent(nlu_result=_TERMINATION_NLU)
    result = agent.process_turn(_turn_input())
    assert result.response_text == "Goodbye."
    agent._llm.call.assert_not_called()


# ---------------------------------------------------------------------------
# Trust output failure
# ---------------------------------------------------------------------------

def test_blocked_output_replaces_response_with_fallback():
    agent = _make_agent(trust_output=BLOCK)
    result = agent.process_turn(_turn_input())
    assert result.response_text == "Output blocked."


def test_blocked_output_still_calls_trust_output():
    agent = _make_agent(trust_output=BLOCK)
    agent.process_turn(_turn_input())
    agent._trust.check_output.assert_called_once()


# ---------------------------------------------------------------------------
# Async post-turn
# ---------------------------------------------------------------------------

def test_memory_write_scheduled_after_return():
    agent = _make_agent()
    agent.process_turn(_turn_input())
    time.sleep(0.1)
    agent._memory.write.assert_called()


def test_learning_emit_scheduled_after_return():
    agent = _make_agent()
    agent.process_turn(_turn_input())
    time.sleep(0.1)
    agent._learning.emit_turn.assert_called_once()
