"""
agent_core/tests/test_orchestrator.py

Unit tests for AgentCore (orchestrator).
All 6 DPG interfaces, ManagerAgent, LanguageNormaliser, NLUProcessor, and AgentWorkflow
are mocked.

Coverage:
- Normal: full turn — both Trust checks called, TurnResult returned
- Normal: tool used — was_tool_used=True in result
- Normal: sync memory writes happen before TurnResult is returned
- Normal: async post-turn runs (memory write + learning emit scheduled)
- Normal: ke_context passed to manager_agent.run_turn
- Edge: empty user_message still processes without error
- Edge: empty messages from build_messages returns empty TurnResult
- Failure: Trust input returns "block" — blocked response returned, LLM not called
- Failure: Trust input returns "escalate" — escalated response, LLM not called
- Failure: Trust output returns "block" — response replaced with fallback message
- Failure: None turn_input raises ValueError
- Failure: empty session_id raises ValueError
- Failure: None workflow raises ValueError
- Routing: unknown intent falls through to LLM via default_fallback
- Routing: global routing intercepts termination_intent
- Routing: session_writes from matched rule written synchronously
- Routing: current_subagent_id written synchronously after routing
- Special handler: hitl subagent escalates without LLM call
- Special handler: whatsapp_handoff returns was_escalated=False
- Config: default_language from config used when no session/profile preference
"""

from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock, ANY

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
    "agent": {},
    "channels": {
        "cli": {"system_prompt_suffix": ""},
        "voice": {"system_prompt_suffix": "Respond in 1-2 short sentences."},
        "web": {"system_prompt_suffix": ""},
    },
    "conversation": {
        "unknown_intent_message": "I didn't understand that.",
        "blocked_message": "Blocked.",
        "escalation_message": "Escalating.",
        "output_blocked_message": "Output blocked.",
    },
    "preprocessing": {
        "nlu_processor": {
            "model": "claude-haiku-test",
            "confidence_threshold": 0.5,
        },
        "language_normalisation": {
            "default_language": "hindi",
        },
    },
    "hitl": {"response_message": "Connecting you to an advisor."},
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


# ---------------------------------------------------------------------------
# Workflow construction helpers
# ---------------------------------------------------------------------------

def _make_subagent(subagent_id: str = "market_truth", special_handler=None) -> MagicMock:
    """Build a minimal mock SubAgent with no routing rules."""
    sa = MagicMock()
    sa.id = subagent_id
    sa.name = f"Test Subagent ({subagent_id})"
    sa.special_handler = special_handler
    sa.system_prompt = f"Test prompt for {subagent_id}."
    sa.output_format = None
    sa.routing = []  # empty — falls through to global_routing, then default_fallback
    sa.opening_phrase = ""  # GH-137: default to empty so opening-phrase gate no-ops in tests
    return sa


def _make_workflow(
    subagent_id: str = "market_truth",
    special_handler=None,
    global_routing=None,
    extra_subagents=None,
) -> MagicMock:
    """Build a minimal mock AgentWorkflow. Routing falls to default_fallback by default."""
    sa = _make_subagent(subagent_id, special_handler)
    subagents = {subagent_id: sa}
    if extra_subagents:
        subagents.update(extra_subagents)

    wf = MagicMock()
    wf.start_subagent_id = subagent_id
    wf.subagents = subagents
    wf.global_routing = global_routing or []
    wf.default_fallback_subagent_id = subagent_id
    wf.nlu_intent_set = {subagent_id: ["market_truth_query"]}
    wf.tool_defs = {}
    wf.agent_system_prompt = ""
    return wf


def _make_agent(
    trust_input: TrustCheckResult = ALLOW,
    trust_output: TrustCheckResult = ALLOW,
    llm_content: str = "LLM response.",
    manager_text: str = "Final response.",
    manager_tool_calls: list = None,
    prompt_messages: list = None,
    nlu_result: NLUResult = None,
    session_data: dict = None,
    workflow: MagicMock = None,
) -> AgentCore:
    """
    Build an AgentCore with all external dependencies mocked.

    LanguageNormaliser and NLUProcessor are replaced on the instance after
    construction so their LLM calls do not interfere with the primary LLM mock.
    """
    session = (
        session_data if session_data is not None
        else {"current_subagent_id": "market_truth"}
    )
    memory = MagicMock()
    memory.context_bundle.return_value = ContextBundle(
        session=session, profile={}, journey=None
    )

    trust = MagicMock()
    trust.check_input.return_value = trust_input
    trust.check_output.return_value = trust_output

    knowledge_engine = MagicMock()

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
        [],
    )

    learning = MagicMock()

    if workflow is None:
        workflow = _make_workflow()

    agent = AgentCore(
        config=VALID_CONFIG,
        llm_wrapper=llm,
        memory=memory,
        trust=trust,
        knowledge_engine=knowledge_engine,
        tool_registry=tool_registry,
        manager_agent=manager,
        learning=learning,
        workflow=workflow,
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
                  MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())


def test_raises_on_none_workflow():
    with pytest.raises(ValueError, match="workflow must not be None"):
        AgentCore(
            config={},
            llm_wrapper=MagicMock(),
            memory=MagicMock(),
            trust=MagicMock(),
            knowledge_engine=MagicMock(),
            tool_registry=MagicMock(),
            manager_agent=MagicMock(),
            learning=MagicMock(),
            workflow=None,
        )


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


def test_manager_run_turn_called_with_ke_context():
    """Agent Core passes ke_context dict (with NLU results) to manager_agent.run_turn."""
    agent = _make_agent(nlu_result=_DEFAULT_NLU)
    agent._language_normaliser.normalise.return_value = ("kaam chahiye", "hinglish")
    agent.process_turn(_turn_input("kaam chahiye"))
    call_kwargs = agent._manager_agent.run_turn.call_args.kwargs
    ke_ctx = call_kwargs.get("ke_context", {})
    assert ke_ctx["intent"] == "market_truth_query"
    assert ke_ctx["entities"] == {"location": "Hubli"}
    assert ke_ctx["normalised_input"] == "kaam chahiye"


# ---------------------------------------------------------------------------
# Sync writes — must complete before TurnResult is returned
# ---------------------------------------------------------------------------

def test_current_subagent_id_written_synchronously():
    """current_subagent_id must be persisted to memory before process_turn returns."""
    agent = _make_agent()
    agent.process_turn(_turn_input())
    agent._memory.write.assert_any_call(
        SESSION_ID, SESSION_ID, "session", "current_subagent_id", ANY
    )


def test_entity_written_synchronously():
    """Entities extracted by NLU are persisted synchronously before result is returned."""
    agent = _make_agent(nlu_result=_DEFAULT_NLU)
    agent.process_turn(_turn_input())
    agent._memory.write.assert_any_call(
        SESSION_ID, SESSION_ID, "persistent", "location", "Hubli"
    )


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
# Routing — intent and workflow behaviour
# ---------------------------------------------------------------------------

def test_unknown_intent_falls_through_to_llm():
    """Unknown intent falls to default_fallback subagent; LLM is still called."""
    agent = _make_agent(nlu_result=_UNKNOWN_NLU)
    agent.process_turn(_turn_input())
    agent._llm.call.assert_called_once()


def test_low_confidence_valid_intent_still_calls_llm():
    """Low confidence alone does NOT skip the LLM when intent is known."""
    low_valid = NLUResult(intent="market_truth_query", entities={}, sentiment="neutral", confidence=0.3)
    agent = _make_agent(nlu_result=low_valid)
    agent.process_turn(_turn_input())
    agent._llm.call.assert_called_once()


def test_termination_intent_routed_via_global_routing():
    """Global routing intercepts termination_intent and routes to the 'ended' subagent."""
    ended_sa = _make_subagent("ended")
    term_rule = MagicMock()
    term_rule.intent = "termination_intent"
    term_rule.next_subagent_id = "ended"
    term_rule.condition = None
    term_rule.conditions = None
    term_rule.session_writes = None

    wf = _make_workflow(
        subagent_id="greeting",
        global_routing=[term_rule],
        extra_subagents={"ended": ended_sa},
    )
    wf.default_fallback_subagent_id = "greeting"
    wf.nlu_intent_set = {"greeting": ["termination_intent"]}

    agent = _make_agent(
        nlu_result=_TERMINATION_NLU,
        session_data={"current_subagent_id": "greeting"},
        workflow=wf,
    )
    agent.process_turn(_turn_input())
    agent._memory.write.assert_any_call(
        SESSION_ID, SESSION_ID, "session", "current_subagent_id", "ended"
    )


def test_session_writes_from_routing_rule_applied():
    """session_writes from a matched routing rule are persisted synchronously."""
    term_rule = MagicMock()
    term_rule.intent = "termination_intent"
    term_rule.next_subagent_id = "market_truth"
    term_rule.condition = None
    term_rule.conditions = None
    term_rule.session_writes = {"user_storage_mode": "anonymous"}

    wf = _make_workflow(global_routing=[term_rule])
    agent = _make_agent(
        nlu_result=_TERMINATION_NLU,
        session_data={"current_subagent_id": "market_truth"},
        workflow=wf,
    )
    agent.process_turn(_turn_input())
    agent._memory.write.assert_any_call(
        SESSION_ID, SESSION_ID, "session", "user_storage_mode", "anonymous"
    )


# ---------------------------------------------------------------------------
# Trust input failures
# ---------------------------------------------------------------------------

def test_blocked_input_returns_blocked_message():
    agent = _make_agent(trust_input=BLOCK)
    result = agent.process_turn(_turn_input())
    assert result.response_text == "Blocked."
    agent._llm.call.assert_not_called()


def test_blocked_input_does_not_call_llm():
    agent = _make_agent(trust_input=BLOCK)
    agent.process_turn(_turn_input())
    agent._llm.call.assert_not_called()


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
# Special handlers (hitl, whatsapp_handoff)
# ---------------------------------------------------------------------------

def test_hitl_special_handler_escalates():
    """A subagent with special_handler='hitl' sets was_escalated=True without LLM call."""
    wf = _make_workflow(subagent_id="hitl_node", special_handler="hitl")
    agent = _make_agent(
        session_data={"current_subagent_id": "hitl_node"},
        workflow=wf,
    )
    result = agent.process_turn(_turn_input())
    assert result.was_escalated is True
    agent._llm.call.assert_not_called()


def test_hitl_special_handler_returns_configured_message():
    """HITL response message is read from config.hitl.response_message, not from LLM."""
    wf = _make_workflow(subagent_id="hitl_node", special_handler="hitl")
    agent = _make_agent(
        session_data={"current_subagent_id": "hitl_node"},
        workflow=wf,
    )
    result = agent.process_turn(_turn_input())
    assert result.response_text == "Connecting you to an advisor."


def test_whatsapp_special_handler_not_escalated():
    """whatsapp_handoff special handler returns was_escalated=False without LLM call."""
    wf = _make_workflow(subagent_id="whatsapp_node", special_handler="whatsapp_handoff")
    agent = _make_agent(
        session_data={"current_subagent_id": "whatsapp_node"},
        workflow=wf,
    )
    result = agent.process_turn(_turn_input())
    assert result.was_escalated is False
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
# Config-driven default language
# ---------------------------------------------------------------------------

def test_default_language_from_config_used_when_no_preference():
    """When no language_preference is in profile or session, config default_language is used."""
    agent = _make_agent(session_data={"current_subagent_id": "market_truth"})
    agent._language_normaliser.normalise.return_value = ("Hello", None)  # no detected language
    agent.process_turn(_turn_input())
    # language_preference should be written as "hindi" (from VALID_CONFIG.preprocessing.language_normalisation.default_language)
    agent._memory.write.assert_any_call(
        SESSION_ID, SESSION_ID, "persistent", "language_preference", "hindi"
    )


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

# ---------------------------------------------------------------------------
# Audit turn recording — Issue #1 regression tests
# ---------------------------------------------------------------------------

def test_blocked_input_records_audit_with_correct_user_id_and_message():
    """record_audit_turn must be called with the real user_id and user_message, not session_id."""
    USER_ID = "user_blocked_001"
    USER_MSG = "What's the market price today?"
    agent = _make_agent(trust_input=BLOCK)
    ti = TurnInput(
        session_id=SESSION_ID,
        user_id=USER_ID,
        user_message=USER_MSG,
        channel="cli",
        timestamp_ms=TIMESTAMP,
    )
    agent.process_turn(ti)
    time.sleep(0.1)

    agent._memory.record_audit_turn.assert_called_once()
    call_kwargs = agent._memory.record_audit_turn.call_args
    assert call_kwargs.kwargs["user_id"] == USER_ID, "user_id must not be session_id for blocked turns"
    assert call_kwargs.kwargs["user_message"] == USER_MSG, "user_message must be the actual message, not 'BLOCKED'"
    assert call_kwargs.kwargs["session_id"] == SESSION_ID
    assert call_kwargs.kwargs["turn_id"] != SESSION_ID, "turn_id must be a UUID, not session_id"


def test_escalated_input_records_audit_with_correct_user_id_and_message():
    """record_audit_turn must be called with the real user_id and user_message for escalated turns."""
    USER_ID = "user_escalated_001"
    USER_MSG = "I want to talk to someone about my loan."
    agent = _make_agent(trust_input=ESCALATE)
    ti = TurnInput(
        session_id=SESSION_ID,
        user_id=USER_ID,
        user_message=USER_MSG,
        channel="cli",
        timestamp_ms=TIMESTAMP,
    )
    agent.process_turn(ti)
    time.sleep(0.1)

    agent._memory.record_audit_turn.assert_called_once()
    call_kwargs = agent._memory.record_audit_turn.call_args
    assert call_kwargs.kwargs["user_id"] == USER_ID, "user_id must not be session_id for escalated turns"
    assert call_kwargs.kwargs["user_message"] == USER_MSG, "user_message must be the actual message, not 'ESCALATED'"
    assert call_kwargs.kwargs["turn_id"] != SESSION_ID, "turn_id must be a UUID, not session_id"


def test_blocked_input_audit_has_non_empty_turn_id():
    """turn_id in audit call must be a non-empty UUID string, not empty or session_id."""
    agent = _make_agent(trust_input=BLOCK)
    agent.process_turn(_turn_input())
    time.sleep(0.1)

    agent._memory.record_audit_turn.assert_called_once()
    call_kwargs = agent._memory.record_audit_turn.call_args
    turn_id = call_kwargs.kwargs["turn_id"]
    assert turn_id, "turn_id must not be empty"
    assert turn_id != SESSION_ID, "turn_id must not equal session_id"


def test_escalated_input_audit_has_non_empty_turn_id():
    """turn_id in audit call must be a non-empty UUID string."""
    agent = _make_agent(trust_input=ESCALATE)
    agent.process_turn(_turn_input())
    time.sleep(0.1)

    agent._memory.record_audit_turn.assert_called_once()
    call_kwargs = agent._memory.record_audit_turn.call_args
    turn_id = call_kwargs.kwargs["turn_id"]
    assert turn_id, "turn_id must not be empty"
    assert turn_id != SESSION_ID, "turn_id must not equal session_id"


# ── Consent gate tests ────────────────────────────────────────────────────

def _make_agent_with_consent(
    consent_prompt: str,
    ask: bool = True,
    trust_input: TrustCheckResult = None,
    trust_output: TrustCheckResult = None,
    session_data: dict = None,
    verify_consent_result: bool = True,
) -> AgentCore:
    """Build AgentCore with consent gate enabled."""
    if trust_input is None:
        trust_input = ALLOW
    if trust_output is None:
        trust_output = ALLOW

    config = {
        **VALID_CONFIG,
        "agent": {
            **VALID_CONFIG.get("agent", {}),
            "ask_for_consent": ask,
            "consent_prompt": consent_prompt,
        },
    }
    session = session_data if session_data is not None else {}

    memory = MagicMock()
    memory.context_bundle.return_value = ContextBundle(
        session=session, profile={}, journey=None
    )

    trust = MagicMock()
    trust.check_input.return_value = trust_input
    trust.check_output.return_value = trust_output
    trust.verify_consent.return_value = verify_consent_result

    knowledge_engine = MagicMock()
    llm = MagicMock()
    from src.models import LLMResponse
    llm.call.return_value = LLMResponse(
        content="LLM response.",
        tool_calls=[],
        stop_reason="end_turn",
        model_used="claude-primary",
    )
    tool_registry = MagicMock()
    tool_registry.get_tool_definitions.return_value = []
    manager = MagicMock()
    manager.build_system_prompt.return_value = ""
    manager.build_messages.return_value = [{"role": "user", "content": "Hello"}]
    manager.run_turn.return_value = ("Final response.", [], [])
    learning = MagicMock()

    agent = AgentCore(
        config=config,
        llm_wrapper=llm,
        memory=memory,
        trust=trust,
        knowledge_engine=knowledge_engine,
        tool_registry=tool_registry,
        manager_agent=manager,
        learning=learning,
        workflow=_make_workflow(),
    )
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("Hello", "english")
    agent._nlu_processor = MagicMock()
    agent._nlu_processor.process.return_value = _DEFAULT_NLU
    return agent, memory, trust


def test_consent_gate_disabled_skips_entirely():
    """ask_for_consent=false → consent gate never entered, verify_consent not called."""
    agent, memory, trust = _make_agent_with_consent(
        consent_prompt="Agree?",
        ask=False,
        session_data={"current_subagent_id": "market_truth"},
    )
    agent.process_turn(_turn_input("hello"))
    trust.verify_consent.assert_not_called()


def test_consent_gate_turn1_returns_prompt():
    """Fresh session (turn_count=0, user_storage_mode=None) → return consent prompt, no LLM.

    Language normaliser returns the default language (hindi) so no translation is triggered,
    and the raw consent text is returned unchanged.
    """
    consent_text = "Kya aap agree karte hain?"
    agent, memory, trust = _make_agent_with_consent(
        consent_prompt=consent_text,
        session_data={"current_subagent_id": None, "turn_count": 0},
    )
    # Normaliser returns the default language → no translation triggered
    agent._language_normaliser.normalise.return_value = ("Kya aap agree karte hain?", "hindi")
    result = agent.process_turn(_turn_input("hello"))
    assert result.response_text == consent_text
    trust.verify_consent.assert_not_called()


def test_consent_gate_turn1_translated_to_detected_language():
    """Turn 1 with Kannada user input → consent prompt is translated to Kannada."""
    consent_text = "Kya aap agree karte hain?"
    translated_text = "Neevu sahomatiyaa?"
    agent, memory, trust = _make_agent_with_consent(
        consent_prompt=consent_text,
        session_data={"current_subagent_id": None, "turn_count": 0},
    )
    agent._language_normaliser.normalise.return_value = ("namaskara", "kannada")
    from src.models import LLMResponse
    agent._llm.call.return_value = LLMResponse(
        content=translated_text,
        tool_calls=[],
        stop_reason="end_turn",
        model_used="claude-primary",
    )
    result = agent.process_turn(_turn_input("namaskara"))
    assert result.response_text == translated_text
    trust.verify_consent.assert_not_called()


def test_consent_gate_turn1_falls_back_on_translation_failure():
    """Turn 1 translation failure → consent prompt returned untranslated."""
    consent_text = "Kya aap agree karte hain?"
    agent, memory, trust = _make_agent_with_consent(
        consent_prompt=consent_text,
        session_data={"current_subagent_id": None, "turn_count": 0},
    )
    agent._language_normaliser.normalise.return_value = ("namaskara", "kannada")
    agent._llm.call.side_effect = RuntimeError("LLM unavailable")
    result = agent.process_turn(_turn_input("namaskara"))
    assert result.response_text == consent_text
    trust.verify_consent.assert_not_called()


def test_consent_gate_turn2_granted_writes_saved():
    """Turn 2, user_storage_mode=None → verify consent, write user_storage_mode='saved'."""
    agent, memory, trust = _make_agent_with_consent(
        consent_prompt="Agree?",
        session_data={"current_subagent_id": None, "turn_count": 1, "user_storage_mode": None},
        verify_consent_result=True,
    )
    agent.process_turn(_turn_input("haan"))
    trust.verify_consent.assert_called_once_with(SESSION_ID, "haan")
    # Verify user_storage_mode="saved" was written
    write_calls = [str(c) for c in memory.write.call_args_list]
    assert any("user_storage_mode" in c and "saved" in c for c in write_calls), \
        "Expected user_storage_mode='saved' to be written to memory"


def test_consent_gate_turn2_declined_writes_anonymous():
    """Turn 2, user declined → write user_storage_mode='anonymous'."""
    agent, memory, trust = _make_agent_with_consent(
        consent_prompt="Agree?",
        session_data={"current_subagent_id": None, "turn_count": 1, "user_storage_mode": None},
        verify_consent_result=False,
    )
    agent.process_turn(_turn_input("nahi"))
    trust.verify_consent.assert_called_once_with(SESSION_ID, "nahi")
    # Verify user_storage_mode="anonymous" was written
    write_calls = [str(c) for c in memory.write.call_args_list]
    assert any("user_storage_mode" in c and "anonymous" in c for c in write_calls), \
        "Expected user_storage_mode='anonymous' to be written to memory"


def test_consent_gate_skipped_when_storage_mode_set():
    """user_storage_mode already set → skip consent gate."""
    agent, memory, trust = _make_agent_with_consent(
        consent_prompt="Agree?",
        session_data={"current_subagent_id": "market_truth", "user_storage_mode": "saved", "turn_count": 3},
    )
    agent.process_turn(_turn_input("electrician kaam chahiye"))
    trust.verify_consent.assert_not_called()


# ---------------------------------------------------------------------------
# OTel span instrumentation
# ---------------------------------------------------------------------------

def test_process_turn_emits_orchestrator_span():
    """orchestrator.turn span must be created for every turn."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry import trace

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    agent = _make_agent()
    agent.process_turn(_turn_input())

    spans = exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert "orchestrator.turn" in span_names, f"Expected 'orchestrator.turn' in {span_names}"
    turn_span = next(s for s in spans if s.name == "orchestrator.turn")
    assert turn_span.attributes.get("session_id") is not None
    assert turn_span.attributes.get("turn_id") is not None


# ---------------------------------------------------------------------------
# #123 — language preference stability across turns
# ---------------------------------------------------------------------------

def test_language_preference_not_overridden_by_auto_detection():
    """When session already has language_preference, a different turn_language must not override it."""
    agent = _make_agent(
        session_data={
            "current_subagent_id": "market_truth",
            "language_preference": "hindi",
        }
    )
    # Simulate auto-detection returning "english" on this turn
    agent._language_normaliser.normalise.return_value = ("Hello", "english")

    agent.process_turn(_turn_input("Hello"))

    # Must NOT have written "english" to memory as language_preference
    for call_args in agent._memory.write.call_args_list:
        args = call_args[0]  # positional args: session_id, user_id, scope, key, value
        if len(args) >= 5 and args[3] == "language_preference":
            assert args[4] == "hindi", (
                f"language_preference was overwritten to {args[4]!r}; expected 'hindi'"
            )


def test_language_preference_set_from_detection_on_first_turn():
    """When no saved preference exists, auto-detection result becomes the preference."""
    agent = _make_agent(
        session_data={"current_subagent_id": "market_truth"}
    )
    agent._language_normaliser.normalise.return_value = ("kaam chahiye", "hindi")

    agent.process_turn(_turn_input("kaam chahiye"))

    agent._memory.write.assert_any_call(
        SESSION_ID, SESSION_ID, "persistent", "language_preference", "hindi"
    )


# ---------------------------------------------------------------------------
# #125 — user-requested language switch
# ---------------------------------------------------------------------------

_SWITCH_NLU = NLUResult(
    intent="language_switch_request",
    entities={"requested_language": "kannada"},
    sentiment="neutral",
    confidence=0.95,
)

_SWITCH_UNSUPPORTED_NLU = NLUResult(
    intent="language_switch_request",
    entities={"requested_language": "french"},
    sentiment="neutral",
    confidence=0.95,
)

VALID_CONFIG_WITH_LANG = {
    **VALID_CONFIG,
    "entity_persistence": {"scope": "persistent"},
    "preprocessing": {
        **VALID_CONFIG.get("preprocessing", {}),
        "language_normalisation": {
            "default_language": "hindi",
            "supported_languages": ["hindi", "kannada", "english", "hinglish"],
        },
    },
    "conversation": {
        **VALID_CONFIG.get("conversation", {}),
        "unsupported_language_message": "Sorry, that language is not supported.",
    },
}


def test_language_switch_to_supported_language_updates_preference():
    """language_switch_request intent with a supported language persists the new preference."""
    agent = _make_agent(
        nlu_result=_SWITCH_NLU,
        session_data={"current_subagent_id": "market_truth", "language_preference": "hindi"},
    )
    agent._config = VALID_CONFIG_WITH_LANG
    agent.process_turn(_turn_input("Kannada mein baat karo"))

    agent._memory.write.assert_any_call(
        SESSION_ID, SESSION_ID, "persistent", "language_preference", "kannada"
    )


def test_language_switch_to_unsupported_language_returns_config_message():
    """language_switch_request with unsupported language returns unsupported_language_message."""
    agent = _make_agent(
        nlu_result=_SWITCH_UNSUPPORTED_NLU,
        session_data={"current_subagent_id": "market_truth"},
    )
    agent._config = VALID_CONFIG_WITH_LANG
    result = agent.process_turn(_turn_input("Please respond in French"))

    assert "Sorry, that language is not supported." in result.response_text


def test_language_switch_to_unsupported_does_not_call_llm():
    """Unsupported language switch returns early without an LLM call."""
    agent = _make_agent(
        nlu_result=_SWITCH_UNSUPPORTED_NLU,
        session_data={"current_subagent_id": "market_truth"},
    )
    agent._config = VALID_CONFIG_WITH_LANG
    agent.process_turn(_turn_input("Please respond in French"))

    agent._llm.call.assert_not_called()


def test_language_switch_to_supported_language_continues_turn():
    """language_switch_request with a valid language does not short-circuit the turn."""
    agent = _make_agent(
        nlu_result=_SWITCH_NLU,
        session_data={"current_subagent_id": "market_truth"},
    )
    agent._config = VALID_CONFIG_WITH_LANG
    result = agent.process_turn(_turn_input("Kannada mein baat karo"))

    assert result.response_text  # manager mock returns "Final response."


# ---------------------------------------------------------------------------
# Channel-aware prompting
# ---------------------------------------------------------------------------


def test_process_turn_unsupported_channel_raises_value_error():
    """process_turn raises ValueError immediately for a channel not in agent.channels."""
    agent = _make_agent()
    turn = TurnInput(
        session_id=SESSION_ID,
        user_message="Hello",
        channel="whatsapp",   # not in VALID_CONFIG agent.channels
        timestamp_ms=TIMESTAMP,
    )
    with pytest.raises(ValueError, match="Unsupported channel: whatsapp"):
        agent.process_turn(turn)


def test_process_turn_passes_channel_config_to_build_system_prompt():
    """channel_config resolved from top-level channels is forwarded to build_system_prompt."""
    agent = _make_agent()
    agent.process_turn(_turn_input())   # channel="cli"
    call_kwargs = agent._manager_agent.build_system_prompt.call_args.kwargs
    assert call_kwargs["channel_config"] == {"system_prompt_suffix": ""}


def test_resolve_channel_config_reads_top_level_channels():
    """_resolve_channel_config returns the entry from the top-level channels block."""
    agent = _make_agent()
    result = agent._resolve_channel_config("voice")
    assert result == {"system_prompt_suffix": "Respond in 1-2 short sentences."}


def test_resolve_channel_config_rejects_legacy_agent_channels():
    """Legacy agent.channels path is rejected at resolve time (GH-137 hard cut)."""
    agent = _make_agent()
    agent._config = {
        **VALID_CONFIG,
        "agent": {"channels": {"voice": {"system_prompt_suffix": "old"}}},
    }
    with pytest.raises(ValueError, match="agent.channels"):
        agent._resolve_channel_config("voice")


# ── Opening-phrase gate tests (GH-137) ───────────────────────────────────────

def test_opening_phrase_emitted_on_first_turn_when_configured():
    """When opening_phrase is set on the start subagent, it is emitted on turn 0."""
    agent = _make_agent(session_data={})   # fresh session — no opening_phrase_emitted flag
    agent._workflow.subagents["market_truth"].opening_phrase = "नमस्ते।"
    agent._workflow.start_subagent_id = "market_truth"

    result = agent.process_turn(_turn_input(""))
    assert result.response_text == "नमस्ते।"


def test_opening_phrase_skipped_when_already_emitted():
    """opening_phrase_emitted flag in session prevents re-emission."""
    agent = _make_agent(
        session_data={"current_subagent_id": "market_truth", "opening_phrase_emitted": True},
    )
    agent._workflow.subagents["market_truth"].opening_phrase = "नमस्ते।"

    result = agent.process_turn(_turn_input("hello"))
    assert result.response_text != "नमस्ते।"


def test_opening_phrase_empty_sets_flag_and_falls_through():
    """Empty opening_phrase still sets opening_phrase_emitted flag; turn proceeds."""
    agent = _make_agent(session_data={"current_subagent_id": "market_truth"})
    agent._workflow.subagents["market_truth"].opening_phrase = ""

    agent.process_turn(_turn_input("hello"))
    # Gate wrote the flag
    agent._memory.write.assert_any_call(
        SESSION_ID, SESSION_ID, "session", "opening_phrase_emitted", True
    )


# ── User-state model init tests (GH-139) ─────────────────────────────────────

def _make_usm_config(enabled: bool) -> dict:
    """Build a minimal config with user_state_model at the correct nesting level."""
    base = {
        **VALID_CONFIG,
        "conversation": {
            **VALID_CONFIG.get("conversation", {}),
        },
    }
    if enabled:
        base["conversation"]["user_state_model"] = {
            "enabled": True,
            "default_state": "fog",
            "states": [
                {"id": "fog", "label": "Fog", "guidance": "User is in fog state."},
                {"id": "aware", "label": "Aware", "guidance": "User is aware."},
            ],
        }
    return base


def test_agentcore_init_user_state_enabled_caches_guidance():
    """AgentCore caches user-state guidance lookup when user_state_model.enabled is true."""
    config = _make_usm_config(enabled=True)
    agent = AgentCore(
        config=config,
        llm_wrapper=MagicMock(),
        memory=MagicMock(),
        trust=MagicMock(),
        knowledge_engine=MagicMock(),
        tool_registry=MagicMock(),
        manager_agent=MagicMock(),
        learning=MagicMock(),
        workflow=_make_workflow(),
    )

    assert agent._user_state_enabled is True
    assert agent._user_state_guidance_by_id["fog"] == "User is in fog state."
    assert agent._user_state_guidance_by_id["aware"] == "User is aware."
    assert agent._user_state_default == "fog"


def test_agentcore_init_user_state_disabled_empty_cache():
    """AgentCore leaves user-state caches empty when user_state_model is absent."""
    config = _make_usm_config(enabled=False)
    agent = AgentCore(
        config=config,
        llm_wrapper=MagicMock(),
        memory=MagicMock(),
        trust=MagicMock(),
        knowledge_engine=MagicMock(),
        tool_registry=MagicMock(),
        manager_agent=MagicMock(),
        learning=MagicMock(),
        workflow=_make_workflow(),
    )

    assert agent._user_state_enabled is False
    assert agent._user_state_guidance_by_id == {}
    assert agent._user_state_default == ""


# ---------------------------------------------------------------------------
# GH-137: session_end_eval + end_session tool registration
# ---------------------------------------------------------------------------


def _config_with_session_end_eval(enabled: bool, prompt: str = "") -> dict:
    """Clone VALID_CONFIG and inject conversation.session_end_eval."""
    cfg = {k: (v.copy() if isinstance(v, dict) else v) for k, v in VALID_CONFIG.items()}
    conv = dict(cfg.get("conversation", {}))
    conv["session_end_eval"] = {"enabled": enabled, "prompt": prompt}
    cfg["conversation"] = conv
    return cfg


def _make_agent_with_config(config: dict, workflow: MagicMock = None) -> AgentCore:
    """Build an AgentCore with a specific config and a real-ish tool_registry mock."""
    memory = MagicMock()
    memory.context_bundle.return_value = ContextBundle(
        session={"current_subagent_id": "market_truth"}, profile={}, journey=None
    )
    trust = MagicMock()
    trust.check_input.return_value = ALLOW
    trust.check_output.return_value = ALLOW
    knowledge_engine = MagicMock()
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content="LLM response.", tool_calls=[], stop_reason="end_turn",
        model_used="claude-primary",
    )
    tool_registry = MagicMock()
    tool_registry.get_tool_definitions.return_value = []
    # Record register_internal calls so tests can assert on them.
    manager = MagicMock()
    manager.build_system_prompt.return_value = ""
    manager.build_messages.return_value = [{"role": "user", "content": "Hello"}]
    manager.run_turn.return_value = ("Final response.", [], [])
    manager.session_ended = False
    learning = MagicMock()
    if workflow is None:
        workflow = _make_workflow()

    agent = AgentCore(
        config=config,
        llm_wrapper=llm,
        memory=memory,
        trust=trust,
        knowledge_engine=knowledge_engine,
        tool_registry=tool_registry,
        manager_agent=manager,
        learning=learning,
        workflow=workflow,
    )
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("Hello", "english")
    agent._nlu_processor = MagicMock()
    agent._nlu_processor.process.return_value = _DEFAULT_NLU
    return agent


def test_session_end_eval_disabled_by_default():
    """VALID_CONFIG has no session_end_eval — orchestrator caches disabled + empty prompt."""
    agent = _make_agent()
    assert agent._session_end_eval_enabled is False
    assert agent._session_end_eval_prompt == ""


def test_session_end_eval_enabled_registers_end_session_tool():
    """When enabled, orchestrator calls tool_registry.register_internal for end_session."""
    cfg = _config_with_session_end_eval(enabled=True, prompt="Call end_session at goodbye.")
    agent = _make_agent_with_config(cfg)
    assert agent._session_end_eval_enabled is True
    assert agent._session_end_eval_prompt == "Call end_session at goodbye."
    # register_internal should have been called with name=end_session, route=orchestrator.
    agent._tool_registry.register_internal.assert_called_once()
    kwargs = agent._tool_registry.register_internal.call_args.kwargs
    assert kwargs["name"] == "end_session"
    assert kwargs["route"] == "orchestrator"
    assert "reason" in kwargs["input_schema"]["properties"]


def test_session_end_eval_disabled_does_not_register_end_session():
    """When disabled, no register_internal call is made."""
    cfg = _config_with_session_end_eval(enabled=False)
    agent = _make_agent_with_config(cfg)
    agent._tool_registry.register_internal.assert_not_called()


def test_session_end_eval_enabled_extends_subagent_tool_defs():
    """When enabled, every subagent's tool_defs entry gains end_session."""
    cfg = _config_with_session_end_eval(enabled=True, prompt="p")
    wf = _make_workflow()
    # Seed with a pre-existing tool in the subagent's defs list.
    wf.tool_defs = {"market_truth": [{"name": "existing_tool"}]}
    _ = _make_agent_with_config(cfg, workflow=wf)
    names = {t["name"] for t in wf.tool_defs["market_truth"]}
    assert "end_session" in names
    assert "existing_tool" in names


def test_session_end_eval_prompt_passed_into_build_system_prompt():
    """process_turn passes session_end_eval_prompt to build_system_prompt when enabled."""
    cfg = _config_with_session_end_eval(enabled=True, prompt="Call end_session at goodbye.")
    agent = _make_agent_with_config(cfg)
    agent.process_turn(_turn_input())
    kwargs = agent._manager_agent.build_system_prompt.call_args.kwargs
    assert kwargs.get("session_end_eval_prompt") == "Call end_session at goodbye."


def test_session_end_eval_prompt_none_when_disabled():
    """When disabled, session_end_eval_prompt arg is None."""
    cfg = _config_with_session_end_eval(enabled=False, prompt="ignored")
    agent = _make_agent_with_config(cfg)
    agent.process_turn(_turn_input())
    kwargs = agent._manager_agent.build_system_prompt.call_args.kwargs
    assert kwargs.get("session_end_eval_prompt") is None


def test_process_turn_threads_session_ended_true_into_turn_result():
    """When manager.session_ended=True, TurnResult.session_ended is True."""
    cfg = _config_with_session_end_eval(enabled=True, prompt="p")
    agent = _make_agent_with_config(cfg)
    agent._manager_agent.session_ended = True
    result = agent.process_turn(_turn_input())
    assert result.session_ended is True


def test_process_turn_session_ended_default_false():
    """When manager.session_ended=False, TurnResult.session_ended is False (default)."""
    agent = _make_agent()
    # Default manager mock has no session_ended attr explicitly set → None/Mock; guard below.
    agent._manager_agent.session_ended = False
    result = agent.process_turn(_turn_input())
    assert result.session_ended is False
