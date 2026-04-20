"""
agent_core/tests/test_manager_agent.py

Unit tests for ManagerAgent.
LLMWrapper, ToolRegistry, ActionGateway, and TrustLayer are all mocked.

Coverage:
- Normal: LLM returns end_turn on first call — no tools executed
- Normal: LLM requests tool, tool executes, LLM returns final response
- Normal: tool_calls list is populated correctly
- Edge: stop_reason is tool_use but tool_calls list is empty — loop exits safely
- Failure: consent denied for write tool — ToolResult error returned to LLM
- Failure: LLM returns error stop_reason — returns empty string gracefully
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.manager_agent import ManagerAgent
from src.models import LLMResponse, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SESSION_ID = "sess_test_001"

MESSAGES = [{"role": "user", "content": "What is my balance?"}]


def _make_manager(
    llm_responses: list[LLMResponse],
    tool_result: ToolResult = None,
    consent_granted: bool = True,
    requires_consent: bool = False,
) -> tuple[ManagerAgent, MagicMock, MagicMock, MagicMock, MagicMock]:
    llm = MagicMock()
    llm.call.side_effect = llm_responses[1:]  # first response is passed directly

    registry = MagicMock()
    registry.requires_consent.return_value = requires_consent
    registry.get_tool_definitions.return_value = []
    registry.get_route.return_value = None  # default: route to Action Gateway

    gateway = MagicMock()
    if tool_result:
        gateway.execute.return_value = tool_result

    ke = MagicMock()

    trust = MagicMock()
    trust.check_consent.return_value = consent_granted

    agent = ManagerAgent(
        llm_wrapper=llm,
        tool_registry=registry,
        action_gateway=gateway,
        knowledge_engine=ke,
        trust_layer=trust,
        max_tool_rounds=1,
    )
    return agent, llm, registry, gateway, trust


def _tool_call() -> ToolCall:
    return ToolCall(
        tool_name="get_balance",
        tool_use_id="tu_abc",
        input_params={"account": "12345"},
    )


def _tool_response_llm(tool_call: ToolCall) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[tool_call],
        stop_reason="tool_use",
        model_used="claude-primary",
    )


def _text_llm_response(text: str = "Your balance is 100.") -> LLMResponse:
    return LLMResponse(
        content=text,
        tool_calls=[],
        stop_reason="end_turn",
        model_used="claude-primary",
    )


# ---------------------------------------------------------------------------
# Init validation
# ---------------------------------------------------------------------------

def test_raises_on_none_llm_wrapper():
    registry = MagicMock()
    gateway = MagicMock()
    ke = MagicMock()
    trust = MagicMock()
    with pytest.raises(ValueError, match="llm_wrapper must not be None"):
        ManagerAgent(None, registry, gateway, ke, trust)


def test_raises_on_none_session_id():
    agent, *_ = _make_manager([_text_llm_response()])
    with pytest.raises(ValueError, match="session_id must not be None"):
        agent.run_turn(MESSAGES, None, _text_llm_response())


def test_raises_on_none_initial_response():
    agent, *_ = _make_manager([_text_llm_response()])
    with pytest.raises(ValueError, match="initial_llm_response must not be None"):
        agent.run_turn(MESSAGES, SESSION_ID, None)


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------

def test_no_tool_call_returns_initial_response_text():
    initial = _text_llm_response("Direct answer.")
    agent, llm, *_ = _make_manager([initial])

    text, tool_calls, _ = agent.run_turn(MESSAGES, SESSION_ID, initial)

    assert text == "Direct answer."
    assert tool_calls == []
    llm.call.assert_not_called()  # No second LLM call needed


def test_tool_call_executes_and_returns_final_text():
    tc = _tool_call()
    initial = _tool_response_llm(tc)
    followup = _text_llm_response("Your balance is $100.")
    tool_result = ToolResult(
        tool_use_id="tu_abc",
        tool_name="get_balance",
        result={"balance": 100},
        success=True,
    )

    agent, llm, registry, gateway, _ = _make_manager(
        llm_responses=[initial, followup],
        tool_result=tool_result,
    )

    text, tool_calls, _ = agent.run_turn(list(MESSAGES), SESSION_ID, initial)

    assert text == "Your balance is $100."
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_name == "get_balance"
    gateway.execute.assert_called_once()
    llm.call.assert_called_once()


def test_tool_calls_list_populated_correctly():
    tc = _tool_call()
    initial = _tool_response_llm(tc)
    followup = _text_llm_response("Done.")
    tool_result = ToolResult("tu_abc", "get_balance", {}, True)

    agent, *_ = _make_manager(
        llm_responses=[initial, followup],
        tool_result=tool_result,
    )

    _, tool_calls, _results = agent.run_turn(list(MESSAGES), SESSION_ID, initial)
    assert tool_calls[0].tool_use_id == "tu_abc"
    assert tool_calls[0].input_params == {"account": "12345"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_tool_use_stop_reason_with_empty_tool_calls_exits_safely():
    initial = LLMResponse(
        content=None,
        tool_calls=[],
        stop_reason="tool_use",
        model_used="claude-primary",
    )
    agent, llm, *_ = _make_manager([initial])

    text, tool_calls, _ = agent.run_turn(MESSAGES, SESSION_ID, initial)

    assert tool_calls == []
    llm.call.assert_not_called()


def test_llm_error_response_returns_empty_string():
    initial = LLMResponse(content=None, tool_calls=[], stop_reason="error")
    agent, *_ = _make_manager([initial])

    text, tool_calls, _ = agent.run_turn(MESSAGES, SESSION_ID, initial)

    assert text == ""
    assert tool_calls == []


# ---------------------------------------------------------------------------
# Failure scenarios
# ---------------------------------------------------------------------------

def test_consent_denied_returns_consent_required_tool_result():
    tc = _tool_call()
    initial = _tool_response_llm(tc)
    followup = _text_llm_response("Please confirm.")

    agent, llm, registry, gateway, trust = _make_manager(
        llm_responses=[initial, followup],
        requires_consent=True,
        consent_granted=False,
    )

    _, tool_calls, _results = agent.run_turn(list(MESSAGES), SESSION_ID, initial)

    # Gateway should NOT be called — consent was denied
    gateway.execute.assert_not_called()

    # LLM should still be called with tool_result containing consent_required error
    llm.call.assert_called_once()
    call_messages = llm.call.call_args.kwargs["messages"]
    tool_result_msg = call_messages[-1]
    assert tool_result_msg["role"] == "user"
    assert "consent_required" in str(tool_result_msg["content"])


# ---------------------------------------------------------------------------
# build_system_prompt — E1
# ---------------------------------------------------------------------------


def _make_manager_for_prompt() -> ManagerAgent:
    agent, *_ = _make_manager([_text_llm_response()])
    return agent


def test_build_system_prompt_includes_persona():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        "You are Kaam Ki Baat, a job advisory assistant.",
        "", "hindi", "cli", {},
    )
    assert "Kaam Ki Baat" in result


def test_build_system_prompt_includes_detected_language():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt("", "", "kannada", "cli", {})
    assert "kannada" in result


def test_build_system_prompt_includes_profile_fields():
    agent = _make_manager_for_prompt()
    profile = {"trade": "electrician", "location": "Hubli"}
    result = agent.build_system_prompt("", "", "hindi", "cli", profile)
    assert "electrician" in result
    assert "Hubli" in result


def test_build_system_prompt_empty_args_returns_string():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt("", "", "", "", {})
    assert isinstance(result, str)


def test_build_system_prompt_guardrails_in_agent_prompt_included():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        "Stay on employment topics. Escalate distress.",
        "", "english", "cli", {},
    )
    assert "employment topics" in result


def test_build_system_prompt_subagent_prompt_included():
    """Subagent system prompt is appended after agent-level prompt."""
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        "You are a domain agent.",
        "## Market truth guidance\nShow ONEST results.",
        "hindi", "cli", {},
    )
    assert "Market truth guidance" in result
    assert "Show ONEST results" in result


def test_build_system_prompt_empty_subagent_prompt_adds_no_extra():
    """Empty subagent prompt does not add extra text to output."""
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt("You are a domain agent.", "", "hindi", "cli", {})
    assert "Market truth guidance" not in result


def test_build_system_prompt_resumption_flag_injects_resumption_text():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt("", "", "hindi", "cli", {}, is_resumption=True)
    assert "resumed" in result.lower() or "returning" in result.lower() or "returned" in result.lower()


def test_build_system_prompt_channel_injected():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt("", "", "", "whatsapp", {})
    assert "whatsapp" in result


def test_build_system_prompt_voice_suffix_appended():
    """Voice channel_config suffix appears as the last section of the prompt."""
    agent = _make_manager_for_prompt()
    channel_config = {"system_prompt_suffix": "Respond in 1-2 short spoken sentences. No bullet points."}
    result = agent.build_system_prompt(
        "You are a domain agent.",
        "Help with jobs.",
        "hindi",
        "voice",
        {},
        channel_config=channel_config,
    )
    assert result.endswith("Respond in 1-2 short spoken sentences. No bullet points.")


def test_build_system_prompt_empty_suffix_does_not_change_output():
    """Empty system_prompt_suffix leaves the prompt unchanged."""
    agent = _make_manager_for_prompt()
    baseline = agent.build_system_prompt("You are a domain agent.", "", "hindi", "web", {})
    result = agent.build_system_prompt(
        "You are a domain agent.",
        "",
        "hindi",
        "web",
        {},
        channel_config={"system_prompt_suffix": ""},
    )
    assert result == baseline


def test_build_system_prompt_suffix_is_after_guardrails():
    """Suffix must appear after the guardrail constraints section."""
    agent = _make_manager_for_prompt()
    channel_config = {"system_prompt_suffix": "Keep it short."}
    guardrails = {
        "prompt_constraints": ["No financial advice"],
        "required_disclosures": [],
    }
    result = agent.build_system_prompt(
        "You are an agent.",
        "",
        "hindi",
        "voice",
        {},
        channel_config=channel_config,
        guardrail_constraints=guardrails,
    )
    guardrail_pos = result.index("No financial advice")
    suffix_pos = result.index("Keep it short.")
    assert suffix_pos > guardrail_pos


def test_build_system_prompt_none_channel_config_no_suffix():
    """channel_config=None (default) produces the same output as not passing it."""
    agent = _make_manager_for_prompt()
    without = agent.build_system_prompt("You are an agent.", "", "hindi", "cli", {})
    with_none = agent.build_system_prompt(
        "You are an agent.", "", "hindi", "cli", {}, channel_config=None
    )
    assert without == with_none


def test_build_system_prompt_user_state_guidance_none_no_section():
    """user_state_guidance=None does not inject a section."""
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance=None,
    )
    assert "Current user state guidance" not in result


def test_build_system_prompt_user_state_guidance_empty_no_section():
    """user_state_guidance="" does not inject a section."""
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance="",
    )
    assert "Current user state guidance" not in result


def test_build_system_prompt_user_state_guidance_rendered():
    """user_state_guidance non-empty renders as a ## Current user state guidance section."""
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance="Orient gently. Surface 2-3 directions.",
    )
    assert "## Current user state guidance" in result
    assert "Orient gently. Surface 2-3 directions." in result
    subagent_idx = result.index("B")
    state_idx = result.index("## Current user state guidance")
    assert state_idx > subagent_idx


def test_build_system_prompt_user_state_guidance_before_guardrails():
    """user_state_guidance section appears between subagent prompt and guardrail constraints."""
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance="UG",
        guardrail_constraints={"prompt_constraints": ["C1"], "required_disclosures": []},
    )
    state_idx = result.index("## Current user state guidance")
    guardrail_idx = result.index("## Guardrail Constraints")
    assert state_idx < guardrail_idx


# ---------------------------------------------------------------------------
# build_messages — E2
# ---------------------------------------------------------------------------


def test_build_messages_returns_single_user_message():
    agent = _make_manager_for_prompt()
    msgs = agent.build_messages("kaam chahiye", "")
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"


def test_build_messages_empty_user_message_returns_resumption_placeholder():
    """Empty user message returns a session resumption placeholder, not an empty list."""
    agent = _make_manager_for_prompt()
    msgs = agent.build_messages("", "")
    assert len(msgs) == 1
    assert "Resuming" in msgs[0]["content"]


def test_build_messages_current_question_prepended():
    agent = _make_manager_for_prompt()
    msgs = agent.build_messages("welder", "Aap kaun sa kaam karte hain?")
    content = msgs[0]["content"]
    assert "Aap kaun sa kaam karte hain?" in content
    assert "welder" in content


def test_build_messages_no_current_question_no_prefix():
    agent = _make_manager_for_prompt()
    msgs = agent.build_messages("hello", "")
    content = msgs[0]["content"]
    assert "Last question asked" not in content


# ---------------------------------------------------------------------------
# Guardrail constraint injection
# ---------------------------------------------------------------------------

def test_system_prompt_includes_guardrail_constraints():
    """prompt_constraints are appended to system prompt when guardrail_constraints provided."""
    manager = _make_manager_for_prompt()
    constraints = {
        "prompt_constraints": ["MUST NOT guarantee outcomes"],
        "required_disclosures": ["Hiring decisions rest with employer"],
        "action_gates": {},
        "refusal_templates": {},
    }
    result = manager.build_system_prompt(
        agent_system_prompt="You are an assistant.",
        subagent_system_prompt="Help with jobs.",
        detected_language="hindi",
        channel="cli",
        profile={},
        guardrail_constraints=constraints,
    )
    assert "MUST NOT guarantee outcomes" in result
    assert "Hiring decisions rest with employer" in result
    assert "Guardrail Constraints" in result


def test_system_prompt_empty_guardrails_unchanged():
    """Empty constraints do not alter the system prompt."""
    manager = _make_manager_for_prompt()
    base_prompt = "You are an assistant."
    empty_constraints = {
        "prompt_constraints": [],
        "required_disclosures": [],
        "action_gates": {},
        "refusal_templates": {},
    }
    result_with_empty = manager.build_system_prompt(
        agent_system_prompt=base_prompt,
        subagent_system_prompt="",
        detected_language="hindi",
        channel="cli",
        profile={},
        guardrail_constraints=empty_constraints,
    )
    result_without = manager.build_system_prompt(
        agent_system_prompt=base_prompt,
        subagent_system_prompt="",
        detected_language="hindi",
        channel="cli",
        profile={},
        guardrail_constraints=None,
    )
    assert result_with_empty == result_without


def test_system_prompt_no_guardrails_backward_compatible():
    """build_system_prompt works without guardrail_constraints arg (default None)."""
    manager = _make_manager_for_prompt()
    result = manager.build_system_prompt(
        agent_system_prompt="You are an assistant.",
        subagent_system_prompt="Help with jobs.",
        detected_language="hindi",
        channel="cli",
        profile={},
    )
    assert "You are an assistant." in result


# ---------------------------------------------------------------------------
# GH-137: session_end_eval + end_session tool
# ---------------------------------------------------------------------------


def test_build_system_prompt_session_end_eval_prompt_rendered():
    """session_end_eval_prompt is rendered as a '## Session-end evaluation' section."""
    manager = _make_manager_for_prompt()
    result = manager.build_system_prompt(
        agent_system_prompt="A",
        subagent_system_prompt="B",
        detected_language="hindi",
        channel="cli",
        profile={},
        session_end_eval_prompt="Call end_session when the user says goodbye.",
    )
    assert "## Session-end evaluation" in result
    assert "Call end_session when the user says goodbye." in result


def test_build_system_prompt_session_end_eval_prompt_none_no_section():
    """When session_end_eval_prompt is None, no section is emitted."""
    manager = _make_manager_for_prompt()
    result = manager.build_system_prompt(
        agent_system_prompt="A",
        subagent_system_prompt="B",
        detected_language="hindi",
        channel="cli",
        profile={},
        session_end_eval_prompt=None,
    )
    assert "Session-end evaluation" not in result
    assert "end_session" not in result


def test_build_system_prompt_session_end_eval_empty_string_no_section():
    """Empty string also renders no section (falsy)."""
    manager = _make_manager_for_prompt()
    result = manager.build_system_prompt(
        agent_system_prompt="A",
        subagent_system_prompt="B",
        detected_language="hindi",
        channel="cli",
        profile={},
        session_end_eval_prompt="",
    )
    assert "Session-end evaluation" not in result


def test_session_ended_flag_defaults_false():
    """Fresh ManagerAgent reports session_ended=False before any turn."""
    agent, *_ = _make_manager([_text_llm_response()])
    assert agent.session_ended is False


def test_end_session_tool_sets_session_ended_and_skips_executor():
    """LLM calling end_session marks the flag, synthesises benign tool_result, and does not call gateway."""
    end_session_call = ToolCall(
        tool_name="end_session",
        tool_use_id="tu_end_1",
        input_params={"reason": "user_goodbye"},
    )
    first = LLMResponse(
        content=None,
        tool_calls=[end_session_call],
        stop_reason="tool_use",
        model_used="claude-primary",
    )
    final = _text_llm_response("Goodbye.")
    agent, llm, _registry, gateway, _trust = _make_manager([first, final])

    text, tool_calls, tool_results = agent.run_turn(MESSAGES, SESSION_ID, first)

    assert agent.session_ended is True
    assert text == "Goodbye."
    assert len(tool_calls) == 1 and tool_calls[0].tool_name == "end_session"
    assert len(tool_results) == 1
    assert tool_results[0].success is True
    assert tool_results[0].tool_name == "end_session"
    # Gateway must NOT be invoked — end_session is orchestrator-routed, no external exec.
    gateway.execute.assert_not_called()
    # Follow-up LLM call is expected (one tool round completed).
    llm.call.assert_called_once()


def test_run_turn_resets_session_ended_flag_each_turn():
    """Calling run_turn resets the session_ended flag at the top of the loop."""
    initial = _text_llm_response("ok")
    agent, *_ = _make_manager([initial])
    # Pre-set the flag and run a no-tool turn; flag must reset to False.
    agent._session_ended_flag = True
    agent.run_turn(MESSAGES, SESSION_ID, initial)
    assert agent.session_ended is False
