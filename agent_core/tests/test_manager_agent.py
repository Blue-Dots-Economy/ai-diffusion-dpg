"""
agent_core/tests/test_manager_agent.py

Unit tests for ManagerAgent.
ChatProvider, ToolRegistry, ActionGateway, and TrustLayer are all mocked.

Coverage:
- Normal: LLM returns end_turn on first call — no tools executed
- Normal: LLM requests tool, tool executes, LLM returns final response
- Normal: tool_calls list is populated correctly
- Edge: stop_reason is tool_use but tool_calls list is empty — loop exits safely
- Failure: consent denied for write tool — ToolResult error returned to LLM
- Failure: LLM returns error stop_reason — returns empty string gracefully
"""

from __future__ import annotations

import time

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import (
    ChatResponse,
    Message,
    SystemPrompt,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from src.manager_agent import ManagerAgent, ungrounded_params
from src.models import ToolCall, ToolResult
from src.tool_results import ToolResultPolicies, TurnToolCache, args_hash


def _flat(prompt) -> str:
    """Concatenate all TextBlock text values into a single string."""
    if isinstance(prompt, SystemPrompt):
        return "\n\n".join(b.text for b in prompt.blocks)
    if isinstance(prompt, str):
        return prompt
    # legacy fallback during migration
    return "\n\n".join(b["text"] for b in prompt)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SESSION_ID = "sess_test_001"

MESSAGES = [Message(role="user", content=[TextBlock(text="What is my balance?")])]


def _make_manager(
    llm_responses: list[ChatResponse],
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
        chat_provider=llm,
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


def _tool_response(tool_call: ToolCall) -> ChatResponse:
    return ChatResponse(
        content=[
            ToolUseBlock(
                tool_use_id=tool_call.tool_use_id,
                tool_name=tool_call.tool_name,
                input=tool_call.input_params or {},
            ),
        ],
        stop_reason="tool_use",
        model_used="claude-primary",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )


def _text_response(text: str = "Your balance is 100.") -> ChatResponse:
    return ChatResponse(
        content=[TextBlock(text=text)],
        stop_reason="end_turn",
        model_used="claude-primary",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )


# Keep legacy aliases so existing test call-sites don't need mass renaming
_tool_response_llm = _tool_response
_text_llm_response = _text_response


# ---------------------------------------------------------------------------
# Init validation
# ---------------------------------------------------------------------------

def test_raises_on_none_chat_provider():
    registry = MagicMock()
    gateway = MagicMock()
    ke = MagicMock()
    trust = MagicMock()
    with pytest.raises(ValueError, match="chat_provider must not be None"):
        ManagerAgent(chat_provider=None, tool_registry=registry, action_gateway=gateway,
                     knowledge_engine=ke, trust_layer=trust)


def test_raises_on_none_session_id():
    agent, *_ = _make_manager([_text_response()])
    with pytest.raises(ValueError, match="session_id must not be None"):
        agent.run_turn(MESSAGES, None, _text_response())


def test_raises_on_none_initial_response():
    agent, *_ = _make_manager([_text_response()])
    with pytest.raises(ValueError, match="initial_response must not be None"):
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
    initial = ChatResponse(
        content=[],
        stop_reason="tool_use",
        model_used="claude-primary",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )
    agent, llm, *_ = _make_manager([initial])

    text, tool_calls, _ = agent.run_turn(MESSAGES, SESSION_ID, initial)

    assert tool_calls == []
    llm.call.assert_not_called()


def test_llm_error_response_returns_empty_string():
    initial = ChatResponse(
        content=[],
        stop_reason="error",
        model_used="claude-primary",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )
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
    request = llm.call.call_args.args[0]   # ChatRequest is a positional arg
    tool_result_msg = request.messages[-1]
    assert tool_result_msg.role == "user"
    assert "consent_required" in str(tool_result_msg.content)


# ---------------------------------------------------------------------------
# build_system_prompt — E1
# ---------------------------------------------------------------------------


def _make_manager_for_prompt() -> ManagerAgent:
    agent, *_ = _make_manager([_text_llm_response()])
    return agent


def test_build_system_prompt_includes_persona():
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt(
        "You are Kaam Ki Baat, a job advisory assistant.",
        "", "hindi", "cli", {},
    ))
    assert "Kaam Ki Baat" in result


def test_build_system_prompt_includes_detected_language():
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt("", "", "kannada", "cli", {}))
    assert "kannada" in result


def test_build_system_prompt_includes_profile_fields():
    agent = _make_manager_for_prompt()
    profile = {"trade": "electrician", "location": "Hubli"}
    result = _flat(agent.build_system_prompt("", "", "hindi", "cli", profile))
    assert "electrician" in result
    assert "Hubli" in result


def test_build_system_prompt_empty_args_returns_mirror_directive():
    # When all content args are empty and no detected_language is set,
    # the mirror directive (GH-313) is injected so the LLM still receives
    # language guidance even without a prior LN call.
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt("", "", "", "", {})
    assert isinstance(result, SystemPrompt)
    flat = _flat(result)
    assert "mirror" in flat.lower() or "detect" in flat.lower()


def test_build_system_prompt_mirror_directive_when_no_detected_language():
    """Mirror directive is injected when detected_language is empty (#313)."""
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt("", "", "", "cli", {}))
    assert "mirror" in result.lower() or "detect" in result.lower()


def test_build_system_prompt_guardrails_in_agent_prompt_included():
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt(
        "Stay on employment topics. Escalate distress.",
        "", "english", "cli", {},
    ))
    assert "employment topics" in result


def test_build_system_prompt_subagent_prompt_included():
    """Subagent system prompt is appended after agent-level prompt."""
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt(
        "You are a domain agent.",
        "## Market truth guidance\nShow ONEST results.",
        "hindi", "cli", {},
    ))
    assert "Market truth guidance" in result
    assert "Show ONEST results" in result


def test_build_system_prompt_empty_subagent_prompt_adds_no_extra():
    """Empty subagent prompt does not add extra text to output."""
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt("You are a domain agent.", "", "hindi", "cli", {}))
    assert "Market truth guidance" not in result


def test_build_system_prompt_resumption_flag_injects_resumption_text():
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt("", "", "hindi", "cli", {}, is_resumption=True))
    assert "resumed" in result.lower() or "returning" in result.lower() or "returned" in result.lower()


def test_build_system_prompt_channel_injected():
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt("", "", "", "whatsapp", {}))
    assert "whatsapp" in result


def test_build_system_prompt_voice_suffix_appended():
    """Voice channel_config suffix appears as the last section of the prompt."""
    agent = _make_manager_for_prompt()
    channel_config = {"system_prompt_suffix": "Respond in 1-2 short spoken sentences. No bullet points."}
    result = _flat(agent.build_system_prompt(
        "You are a domain agent.",
        "Help with jobs.",
        "hindi",
        "voice",
        {},
        channel_config=channel_config,
    ))
    assert "Respond in 1-2 short spoken sentences. No bullet points." in result


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
    """Suffix (channel_rules) and guardrail constraints both appear in the assembled prompt.

    In the tiered structure, channel_rules live in tier1 (static, ephemeral-cached) and
    active_guardrails live in tier3 (dynamic). Both sections are present in the flat output.
    """
    agent = _make_manager_for_prompt()
    channel_config = {"system_prompt_suffix": "Keep it short."}
    guardrails = {
        "prompt_constraints": ["No financial advice"],
        "required_disclosures": [],
    }
    result = _flat(agent.build_system_prompt(
        "You are an agent.",
        "",
        "hindi",
        "voice",
        {},
        channel_config=channel_config,
        guardrail_constraints=guardrails,
    ))
    assert "Keep it short." in result
    assert "No financial advice" in result


def test_build_system_prompt_none_channel_config_no_suffix():
    """channel_config=None (default) produces the same output as not passing it."""
    agent = _make_manager_for_prompt()
    without = agent.build_system_prompt("You are an agent.", "", "hindi", "cli", {})
    with_none = agent.build_system_prompt(
        "You are an agent.", "", "hindi", "cli", {}, channel_config=None
    )
    assert without == with_none


def test_build_system_prompt_omits_cache_hint_when_provider_lacks_caching():
    """Regression: a provider with supports_prompt_cache=False (e.g. OpenAI today)
    must not receive cache_hint='session' on tier-1/tier-2 blocks. Otherwise
    _validate_request raises UnsupportedFeatureError on every Step-8 LLM call.
    """
    from src.chat_provider.base import Capabilities

    no_cache_caps = Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=False,
        supports_image_input=True,
        supports_audio_input=False,
        supports_structured_output=True,
        supports_force_tool_choice=True,
    )
    agent, *_ = _make_manager([_text_llm_response()])
    agent._llm.capabilities = no_cache_caps

    prompt = agent.build_system_prompt(
        agent_system_prompt="You are helpful.",
        subagent_system_prompt="Help with jobs.",
        detected_language="hindi", channel="cli", profile={},
    )
    for block in prompt.blocks:
        assert block.cache_hint is None


def test_build_system_prompt_sets_cache_hint_when_provider_supports_caching():
    """Anthropic-shaped capabilities → tier-1 + tier-2 blocks carry cache_hint='session'."""
    from src.chat_provider.base import Capabilities

    cache_caps = Capabilities(
        supports_tools=True,
        supports_streaming=True,
        supports_prompt_cache=True,
        supports_image_input=True,
        supports_audio_input=False,
        supports_structured_output=True,
        supports_force_tool_choice=True,
    )
    agent, *_ = _make_manager([_text_llm_response()])
    agent._llm.capabilities = cache_caps

    prompt = agent.build_system_prompt(
        agent_system_prompt="You are helpful.",
        subagent_system_prompt="Help with jobs.",
        detected_language="hindi", channel="cli", profile={},
    )
    # First two blocks (tier 1 + tier 2) carry cache_hint; tier 3 does not.
    assert prompt.blocks[0].cache_hint == "session"
    assert prompt.blocks[1].cache_hint == "session"
    if len(prompt.blocks) >= 3:
        assert prompt.blocks[2].cache_hint is None


def test_build_system_prompt_user_state_guidance_none_no_section():
    """user_state_guidance=None does not inject a section."""
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance=None,
    ))
    assert "<user_state_guidance>" not in result


def test_build_system_prompt_user_state_guidance_empty_no_section():
    """user_state_guidance="" does not inject a section."""
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance="",
    ))
    assert "<user_state_guidance>" not in result


def test_build_system_prompt_user_state_guidance_rendered():
    """user_state_guidance non-empty renders inside a <user_state_guidance> XML section."""
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance="Orient gently. Surface 2-3 directions.",
    ))
    assert "<user_state_guidance>" in result
    assert "Orient gently. Surface 2-3 directions." in result
    subagent_idx = result.index("<subagent>")
    state_idx = result.index("<user_state_guidance>")
    assert state_idx > subagent_idx


def test_build_system_prompt_user_state_guidance_before_guardrails():
    """user_state_guidance section appears between subagent prompt and guardrail constraints."""
    agent = _make_manager_for_prompt()
    result = _flat(agent.build_system_prompt(
        agent_system_prompt="A", subagent_system_prompt="B",
        detected_language="hindi", channel="cli", profile={},
        user_state_guidance="UG",
        guardrail_constraints={"prompt_constraints": ["C1"], "required_disclosures": []},
    ))
    state_idx = result.index("<user_state_guidance>")
    guardrail_idx = result.index("<active_guardrails>")
    assert state_idx < guardrail_idx


# ---------------------------------------------------------------------------
# build_messages — E2
# ---------------------------------------------------------------------------


def test_build_messages_returns_single_user_message():
    agent = _make_manager_for_prompt()
    msgs = agent.build_messages("kaam chahiye", "")
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert isinstance(msgs[0].content[0], TextBlock)


def test_build_messages_empty_user_message_returns_resumption_placeholder():
    """Empty user message returns a session resumption placeholder, not an empty list."""
    agent = _make_manager_for_prompt()
    msgs = agent.build_messages("", "")
    assert len(msgs) == 1
    assert "Resuming" in msgs[0].content[0].text


def test_build_messages_current_question_prepended():
    agent = _make_manager_for_prompt()
    msgs = agent.build_messages("welder", "Aap kaun sa kaam karte hain?")
    content = msgs[0].content[0].text
    assert "Aap kaun sa kaam karte hain?" in content
    assert "welder" in content


def test_build_messages_no_current_question_no_prefix():
    agent = _make_manager_for_prompt()
    msgs = agent.build_messages("hello", "")
    content = msgs[0].content[0].text
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
    result = _flat(manager.build_system_prompt(
        agent_system_prompt="You are an assistant.",
        subagent_system_prompt="Help with jobs.",
        detected_language="hindi",
        channel="cli",
        profile={},
        guardrail_constraints=constraints,
    ))
    assert "MUST NOT guarantee outcomes" in result
    assert "Hiring decisions rest with employer" in result
    assert "<active_guardrails>" in result


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
    result = _flat(manager.build_system_prompt(
        agent_system_prompt="You are an assistant.",
        subagent_system_prompt="Help with jobs.",
        detected_language="hindi",
        channel="cli",
        profile={},
    ))
    assert "You are an assistant." in result


# ---------------------------------------------------------------------------
# GH-137: session_end_eval + end_session tool
# ---------------------------------------------------------------------------


def test_build_system_prompt_session_end_eval_prompt_rendered():
    """session_end_eval_prompt is rendered inside a <session_end_policy> XML section."""
    manager = _make_manager_for_prompt()
    result = _flat(manager.build_system_prompt(
        agent_system_prompt="A",
        subagent_system_prompt="B",
        detected_language="hindi",
        channel="cli",
        profile={},
        session_end_eval_prompt="Call end_session when the user says goodbye.",
    ))
    assert "<session_end_policy>" in result
    assert "Call end_session when the user says goodbye." in result


def test_build_system_prompt_session_end_eval_prompt_none_no_section():
    """When session_end_eval_prompt is None, no section is emitted."""
    manager = _make_manager_for_prompt()
    result = _flat(manager.build_system_prompt(
        agent_system_prompt="A",
        subagent_system_prompt="B",
        detected_language="hindi",
        channel="cli",
        profile={},
        session_end_eval_prompt=None,
    ))
    assert "<session_end_policy>" not in result
    assert "end_session" not in result


def test_build_system_prompt_session_end_eval_empty_string_no_section():
    """Empty string also renders no section (falsy)."""
    manager = _make_manager_for_prompt()
    result = _flat(manager.build_system_prompt(
        agent_system_prompt="A",
        subagent_system_prompt="B",
        detected_language="hindi",
        channel="cli",
        profile={},
        session_end_eval_prompt="",
    ))
    assert "<session_end_policy>" not in result


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
    first = ChatResponse(
        content=[
            ToolUseBlock(
                tool_use_id="tu_end_1",
                tool_name="end_session",
                input={"reason": "user_goodbye"},
            ),
        ],
        stop_reason="tool_use",
        model_used="claude-primary",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )
    final = _text_response("Goodbye.")
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


# ── Layered tiers (GH-176) ────────────────────────────────────────────────
# Contract:
#   build_system_prompt returns list[dict] — Anthropic content blocks.
#   Tier 1 (persona + channel_rules + session_end_policy) carries cache_control.
#   Tier 2 (subagent + user_state_guidance) carries cache_control.
#   Tier 3 (channel_context + resumption + known_profile + active_guardrails) no cache_control.
#   Each populated section is wrapped in a single XML tag; empty inputs elide sections.


def test_build_system_prompt_returns_system_prompt():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt("Persona.", "Subagent.", "hindi", "cli", {})
    assert isinstance(result, SystemPrompt)
    assert all(isinstance(b, TextBlock) for b in result.blocks)


def test_build_system_prompt_tier1_has_cache_hint():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        "Persona text.", "Subagent text.", "hindi", "cli", {},
        channel_config={"system_prompt_suffix": "Voice rules."},
        session_end_eval_prompt="End eval.",
    )
    tier1 = result.blocks[0]
    assert tier1.cache_hint == "session"
    assert "<persona>" in tier1.text
    assert "Persona text." in tier1.text
    assert "<channel_rules>" in tier1.text
    assert "Voice rules." in tier1.text
    assert "<session_end_policy>" in tier1.text
    assert "End eval." in tier1.text


def test_build_system_prompt_tier2_has_cache_hint():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        "Persona.", "Subagent body.", "hindi", "cli", {},
        user_state_guidance="User-state body.",
    )
    # tier 2 is second block when tier 1 is present
    tier2 = result.blocks[1]
    assert tier2.cache_hint == "session"
    assert "<subagent>" in tier2.text
    assert "Subagent body." in tier2.text
    assert "<user_state_guidance>" in tier2.text
    assert "User-state body." in tier2.text


def test_build_system_prompt_tier3_has_no_cache_hint():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        "P.", "S.", "hindi", "cli", {"name": "Rahul"},
        guardrail_constraints={"prompt_constraints": ["Be honest."]},
    )
    # tier 3 is the last block; it exists because profile is non-empty
    tier3 = result.blocks[-1]
    assert tier3.cache_hint is None
    assert "<known_profile>" in tier3.text
    assert "Rahul" in tier3.text
    assert "<active_guardrails>" in tier3.text
    assert "Be honest." in tier3.text


def test_build_system_prompt_elides_empty_sections():
    agent = _make_manager_for_prompt()
    # persona + mirror directive (GH-313) — no channel suffix, no subagent, no profile, no guardrails
    result = agent.build_system_prompt("Persona only.", "", "", "", {})
    flat = _flat(result)
    assert "Persona only." in flat
    assert "<channel_rules>" not in flat
    assert "<session_end_policy>" not in flat


def test_build_system_prompt_resumption_lives_in_tier3():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt("P.", "S.", "hindi", "cli", {}, is_resumption=True)
    tier3 = result.blocks[-1]
    assert tier3.cache_hint is None
    assert "<resumption>" in tier3.text


def test_build_system_prompt_channel_context_lives_in_tier3():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt("P.", "S.", "hindi", "cli", {})
    tier3 = result.blocks[-1]
    assert tier3.cache_hint is None
    assert "<channel_context>" in tier3.text
    assert "cli" in tier3.text
    assert "hindi" in tier3.text


def test_build_system_prompt_xml_tags_are_balanced():
    agent = _make_manager_for_prompt()
    result = agent.build_system_prompt(
        "P.", "S.", "hindi", "cli", {"name": "Rahul"},
        channel_config={"system_prompt_suffix": "Voice."},
        session_end_eval_prompt="End.",
        user_state_guidance="State.",
        is_resumption=True,
        guardrail_constraints={"prompt_constraints": ["x"], "required_disclosures": ["y"]},
    )
    full_text = "\n".join(b.text for b in result.blocks)
    for tag in ("persona", "channel_rules", "session_end_policy",
                "subagent", "user_state_guidance",
                "channel_context", "resumption", "known_profile", "active_guardrails"):
        assert f"<{tag}>" in full_text, f"missing <{tag}>"
        assert f"</{tag}>" in full_text, f"missing </{tag}>"


# ---------------------------------------------------------------------------
# _is_collected — which profile values count as "already collected"
# ---------------------------------------------------------------------------


class TestGroundingSurvivesCacheInvalidation:
    """A returning caller could not apply at all: save_profile correctly
    invalidates the cached fetch_profile, and on the NEXT turn apply_job had no
    evidence for profile_item_id. Session values written by session_mapping
    came from the upstream result, so they ground it and outlive the cache."""

    SPEC = {"profile_item_id": ["fetch_profile", "save_profile"]}
    PID = "37f9420b-9477-4067-9189-eef2abd74a46"

    def _call(self, value):
        return ToolCall(tool_name="apply_job", tool_use_id="t",
                        input_params={"profile_item_id": value})

    def test_session_value_grounds_when_the_cache_is_gone(self):
        assert ungrounded_params(
            self.SPEC, self._call(self.PID), [],
            stored_results={"fetch_jobs": ["unrelated"]},
            session_grounded={"profile_item_id": [self.PID]},
        ) == set()

    def test_without_the_session_value_it_is_still_refused(self):
        assert ungrounded_params(
            self.SPEC, self._call(self.PID), [],
            stored_results={"fetch_jobs": ["unrelated"]},
        ) == {"profile_item_id"}

    def test_an_invented_id_is_still_refused(self):
        """The guard must keep its teeth: a session value for the param does
        not license a DIFFERENT value for it."""
        assert ungrounded_params(
            self.SPEC, self._call("00000000-dead-beef-0000-000000000000"), [],
            stored_results={"fetch_jobs": ["unrelated"]},
            session_grounded={"profile_item_id": [self.PID]},
        ) == {"profile_item_id"}

    def test_an_empty_session_value_grounds_nothing(self):
        assert ungrounded_params(
            self.SPEC, self._call(self.PID), [],
            stored_results={"fetch_jobs": ["unrelated"]},
            session_grounded={"profile_item_id": [""]},
        ) == {"profile_item_id"}


class TestIsCollected:
    """Guards the age-rendered-as-zero defect.

    `age` is the only integer profile field and its unset default is 0. The
    old sentinel list (None, "", [], "[]") did not catch it, so a brand-new
    session rendered `age: 0` under "Already collected — do NOT ask", the
    agent never asked, and the profile API rejected the save as under-18.
    """

    def test_zero_age_is_not_collected(self):
        from src.manager_agent import _is_collected
        assert _is_collected(0) is False

    def test_a_real_age_is_collected(self):
        from src.manager_agent import _is_collected
        assert _is_collected(24) is True

    def test_empty_string_fields_stay_uncollected(self):
        from src.manager_agent import _is_collected
        for empty in (None, "", "[]", [], {}):
            assert _is_collected(empty) is False, empty

    def test_populated_values_stay_collected(self):
        from src.manager_agent import _is_collected
        for filled in ("Ravi Kumar", "Bengaluru", ["a"], {"k": "v"}, 1):
            assert _is_collected(filled) is True, filled

    def test_booleans_are_not_treated_as_numbers(self):
        """False must read as absent, True as present — not as 0 and 1."""
        from src.manager_agent import _is_collected
        assert _is_collected(False) is False
        assert _is_collected(True) is True

    @staticmethod
    def _agent():
        from unittest.mock import MagicMock
        from src.manager_agent import ManagerAgent
        return ManagerAgent(
            chat_provider=MagicMock(),
            tool_registry=MagicMock(),
            action_gateway=MagicMock(),
            knowledge_engine=MagicMock(),
            trust_layer=MagicMock(),
            max_tool_rounds=1,
        )

    def test_zero_age_is_absent_from_the_rendered_prompt(self):
        """The end-to-end shape: a zero age must not reach the LLM."""
        prompt = self._agent().build_system_prompt(
            agent_system_prompt="agent",
            subagent_system_prompt="subagent",
            detected_language="hindi",
            channel="web",
            profile={"name": "Ravi", "age": 0, "location": ""},
            channel_config={},
        )
        text = prompt.text if hasattr(prompt, "text") else str(prompt)
        assert "age: 0" not in text
        assert "Ravi" in text

    def test_a_real_age_does_reach_the_prompt(self):
        """The counterpart: a real age must still be shown as collected.

        orchestrator.py records an earlier defect where a stale 0 outranked a
        fresh 25; this asserts the fix here does not swing the other way and
        start hiding genuine ages.
        """
        prompt = self._agent().build_system_prompt(
            agent_system_prompt="agent",
            subagent_system_prompt="subagent",
            detected_language="hindi",
            channel="web",
            profile={"name": "Ravi", "age": 25},
            channel_config={},
        )
        text = prompt.text if hasattr(prompt, "text") else str(prompt)
        assert "age: 25" in text


class TestToolSessionValues:
    """_tool_session_values must not pass a seeded default off as an answer.

    Session state pre-seeds every profile field — strings as "" and the one
    integer field, age, as 0. Sending that 0 as a real age had the
    participant API reject it as under-18, which the agent then relayed to
    adult callers.
    """

    @staticmethod
    def _bundle(session, profile):
        from types import SimpleNamespace
        return SimpleNamespace(session=session, profile=profile)

    def test_seeded_zero_age_is_not_forwarded(self):
        from src.orchestrator import AgentCore
        v = AgentCore._tool_session_values(self._bundle({"age": 0, "name": ""}, {}))
        assert "age" not in v and "name" not in v

    def test_real_profile_value_beats_the_seeded_session_default(self):
        from src.orchestrator import AgentCore
        v = AgentCore._tool_session_values(self._bundle({"age": 0}, {"age": "27"}))
        assert v["age"] == "27"

    def test_attributes_blob_is_excluded(self):
        from src.orchestrator import AgentCore
        v = AgentCore._tool_session_values(
            self._bundle({}, {"attributes": [{"key": "k", "value": "x"}], "age": "31"})
        )
        assert "attributes" not in v and v["age"] == "31"

    def test_missing_bundle_fields_are_safe(self):
        from src.orchestrator import AgentCore
        from types import SimpleNamespace
        assert AgentCore._tool_session_values(SimpleNamespace()) == {}


# ---------------------------------------------------------------------------
# grounded_params — refuse an identifier the model invented.
#
# A model asked to reproduce a 36-character UUID many turns after it was shown
# will sometimes emit a well-formed one that refers to nothing. The upstream
# cannot distinguish that from a stale id: it answers with a generic "not
# found", which the agent then relays to the caller as if their request had
# merely been redundant. Measured on a live local call — the caller was told
# "you have already applied" for an application that was never created.
# ---------------------------------------------------------------------------


def _tool_result_msg(text: str):
    """One user message carrying a single tool_result block."""
    return Message(
        role="user",
        content=[ToolResultBlock(tool_use_id="t1", content=text)],
    )


def _grounding_agent(grounded):
    return ManagerAgent(
        chat_provider=MagicMock(),
        tool_registry=MagicMock(),
        action_gateway=MagicMock(),
        knowledge_engine=MagicMock(),
        trust_layer=MagicMock(),
        grounded_params=grounded,
    )


def _call(tool_name: str, params: dict):
    return SimpleNamespace(tool_name=tool_name, input_params=params)


def test_ungrounded_param_detected_when_value_absent_from_tool_results():
    agent = _grounding_agent({"apply_job": ["job_item_id"]})
    messages = [_tool_result_msg('{"item_id": "038a09fa-6497-4672-b06c-e1cc580e17ff"}')]
    call = _call("apply_job", {"job_item_id": "835d12cf-4a66-4fab-9e42-873e2f4d8b65"})
    assert agent._ungrounded_params(call, messages) == {"job_item_id"}


def test_grounded_param_accepted_when_value_present():
    agent = _grounding_agent({"apply_job": ["job_item_id"]})
    real = "038a09fa-6497-4672-b06c-e1cc580e17ff"
    messages = [_tool_result_msg('{"item_id": "%s"}' % real)]
    assert agent._ungrounded_params(_call("apply_job", {"job_item_id": real}), messages) == set()


def test_unconfigured_tool_is_never_checked():
    agent = _grounding_agent({"apply_job": ["job_item_id"]})
    messages = [_tool_result_msg("{}")]
    call = _call("fetch_jobs", {"job_item_id": "anything-at-all"})
    assert agent._ungrounded_params(call, messages) == set()


def test_missing_or_empty_param_is_not_flagged():
    """Absence is a different failure; this guard only judges supplied values."""
    agent = _grounding_agent({"apply_job": ["job_item_id"]})
    messages = [_tool_result_msg("some prior result")]
    assert agent._ungrounded_params(_call("apply_job", {}), messages) == set()
    assert agent._ungrounded_params(_call("apply_job", {"job_item_id": ""}), messages) == set()


def test_no_tool_results_yet_lets_the_call_through():
    """Nothing fetched means nothing can be grounded — do not block a first call."""
    agent = _grounding_agent({"apply_job": ["job_item_id"]})
    call = _call("apply_job", {"job_item_id": "835d12cf-4a66-4fab-9e42-873e2f4d8b65"})
    assert agent._ungrounded_params(call, []) == set()


def test_all_configured_params_are_checked_independently():
    agent = _grounding_agent({"apply_job": ["job_item_id", "profile_item_id"]})
    messages = [_tool_result_msg("profile is 830f6326-d274-4a4f-93fd-dee3c6592d58")]
    call = _call("apply_job", {
        "job_item_id": "835d12cf-4a66-4fab-9e42-873e2f4d8b65",   # invented
        "profile_item_id": "830f6326-d274-4a4f-93fd-dee3c6592d58",  # real
    })
    assert agent._ungrounded_params(call, messages) == {"job_item_id"}


def test_value_grounded_in_any_earlier_message_not_just_the_last():
    agent = _grounding_agent({"apply_job": ["job_item_id"]})
    real = "038a09fa-6497-4672-b06c-e1cc580e17ff"
    messages = [_tool_result_msg(real), _tool_result_msg("unrelated later result")]
    assert agent._ungrounded_params(_call("apply_job", {"job_item_id": real}), messages) == set()


def test_no_grounded_params_configured_is_a_no_op():
    agent = _grounding_agent(None)
    messages = [_tool_result_msg("{}")]
    call = _call("apply_job", {"job_item_id": "invented"})
    assert agent._ungrounded_params(call, messages) == set()


# --- source-aware grounding -------------------------------------------------
# A provenance check alone is not enough: every identifier in play is a UUID
# that appeared in SOME tool result, so the model can copy one into another's
# slot and pass. Measured on a live streaming call — apply_job was sent with
# job_item_id == profile_item_id (both 343954ce-…), which the plain check
# allowed because save_profile had indeed returned that value.


def _use(tool_use_id: str, tool_name: str):
    return Message(
        role="assistant",
        content=[ToolUseBlock(tool_use_id=tool_use_id, tool_name=tool_name, input={})],
    )


def _result(tool_use_id: str, text: str):
    return Message(role="user", content=[ToolResultBlock(tool_use_id=tool_use_id, content=text)])


PROFILE_ID = "343954ce-a21f-43b3-8fa1-e5ecf6c5a2a7"
JOB_ID = "038a09fa-6497-4672-b06c-e1cc580e17ff"


def _two_tool_history():
    return [
        _use("u1", "fetch_jobs"), _result("u1", '{"item_id": "%s"}' % JOB_ID),
        _use("u2", "save_profile"), _result("u2", '{"item_id": "%s"}' % PROFILE_ID),
    ]


def test_param_copied_from_the_wrong_tool_is_rejected():
    agent = _grounding_agent({"apply_job": {"job_item_id": ["fetch_jobs"]}})
    call = _call("apply_job", {"job_item_id": PROFILE_ID})
    assert agent._ungrounded_params(call, _two_tool_history()) == {"job_item_id"}


def test_param_from_its_declared_source_is_accepted():
    agent = _grounding_agent({"apply_job": {"job_item_id": ["fetch_jobs"]}})
    call = _call("apply_job", {"job_item_id": JOB_ID})
    assert agent._ungrounded_params(call, _two_tool_history()) == set()


def test_any_of_several_declared_sources_grounds_the_value():
    agent = _grounding_agent(
        {"apply_job": {"job_item_id": ["fetch_recommended_jobs", "fetch_jobs"]}}
    )
    call = _call("apply_job", {"job_item_id": JOB_ID})
    assert agent._ungrounded_params(call, _two_tool_history()) == set()


def test_declared_source_that_never_ran_rejects_the_value():
    """The value cannot have come from a tool that produced no result."""
    agent = _grounding_agent({"apply_job": {"job_item_id": ["fetch_recommended_jobs"]}})
    call = _call("apply_job", {"job_item_id": JOB_ID})
    assert agent._ungrounded_params(call, _two_tool_history()) == {"job_item_id"}


def test_list_form_still_means_any_tool_result():
    """Backwards compatibility: a bare list keeps the original semantics."""
    agent = _grounding_agent({"apply_job": ["job_item_id"]})
    call = _call("apply_job", {"job_item_id": PROFILE_ID})
    assert agent._ungrounded_params(call, _two_tool_history()) == set()


def test_stored_result_grounds_value_when_exchange_not_in_messages():
    from src.manager_agent import ungrounded_params
    spec = {"profile_item_id": ["fetch_profile"]}
    tc = _call("apply_job", {"profile_item_id": "real-id"})
    stored = {"fetch_profile": ['{"items":[{"item_id":"real-id"}]}']}
    assert ungrounded_params(spec, tc, [], stored_results=stored) == set()


def test_stored_result_from_other_tool_does_not_ground():
    from src.manager_agent import ungrounded_params
    spec = {"profile_item_id": ["fetch_profile"]}
    tc = _call("apply_job", {"profile_item_id": "job-id"})
    stored = {"fetch_jobs": ['[{"item_id":"job-id"}]']}
    assert ungrounded_params(spec, tc, [], stored_results=stored) == {"profile_item_id"}


def test_strict_rejects_when_nothing_seen():
    from src.manager_agent import ungrounded_params
    tc = _call("remember", {"value": "x"})
    assert ungrounded_params({"value": ["fetch_profile"]}, tc, [], strict=True) == {"value"}
    assert ungrounded_params({"value": ["fetch_profile"]}, tc, []) == set()   # lenient default unchanged


# --- max_calls_per_turn ----------------------------------------------------
# A prompt rule cannot gate an irreversible write. Measured on live calls: a
# caller picked ONE job and the agent emitted five apply_job calls in one turn
# (three created), then two in another turn (both created) — applications at
# employers the caller never chose, each going to a real hiring manager.


def test_over_call_cap_allows_the_first_call():
    from src.manager_agent import over_call_cap
    assert over_call_cap(1, 0) is False


def test_over_call_cap_blocks_the_second():
    from src.manager_agent import over_call_cap
    assert over_call_cap(1, 1) is True


def test_no_cap_configured_never_blocks():
    from src.manager_agent import over_call_cap
    assert over_call_cap(None, 99) is False
    assert over_call_cap(0, 99) is False


def test_a_non_integer_cap_is_ignored_rather_than_raising():
    """A guard that raises is worse than no guard."""
    from src.manager_agent import over_call_cap
    from unittest.mock import MagicMock
    assert over_call_cap(MagicMock(), 3) is False
    assert over_call_cap("1", 3) is False
    assert over_call_cap(True, 3) is False


def test_cap_of_two_allows_two_then_blocks():
    from src.manager_agent import over_call_cap
    assert over_call_cap(2, 0) is False
    assert over_call_cap(2, 1) is False
    assert over_call_cap(2, 2) is True


def test_run_turn_ungrounded_refusal_does_not_crash():
    """An invented id must produce a refusal tool_result, not a TypeError."""
    tc = ToolCall(tool_name="apply_job", tool_use_id="tu_apply",
                  input_params={"profile_item_id": "invented-id"})
    initial = _tool_response(tc)
    followup = _text_response("Let me check that again.")
    agent, llm, registry, gateway, _ = _make_manager(llm_responses=[initial, followup])
    agent._grounded_params = {"apply_job": {"profile_item_id": ["fetch_profile"]}}
    earlier = [
        Message(role="assistant", content=[ToolUseBlock(
            tool_use_id="tu_fp", tool_name="fetch_profile", input={})]),
        Message(role="user", content=[ToolResultBlock(
            tool_use_id="tu_fp", content='{"items":[{"item_id":"real-id"}]}')]),
    ]

    text, _, results = agent.run_turn(earlier + list(MESSAGES), SESSION_ID, initial)

    assert text == "Let me check that again."
    gateway.execute.assert_not_called()
    assert results[0].tool_use_id == "tu_apply"
    assert results[0].error == "UNGROUNDED_PARAMETER"


# ---------------------------------------------------------------------------
# Tool-result persistence (sync path)
# ---------------------------------------------------------------------------

_POL = ToolResultPolicies.from_config({"connectors": {"read": [
    {"name": "get_balance", "cache": {"scope": "session", "ttl_seconds": 600}}]}})


def _entry():
    return {"tool": "get_balance", "args_hash": args_hash({"account": "12345"}),
            "data": {"balance": 100}, "fetched_at": time.time(), "expires_at": 9e12,
            "origin": "turn", "scope": "session"}


def test_run_turn_serves_cache_hit_without_gateway():
    tc = _tool_call()
    agent, llm, _, gateway, _ = _make_manager([_tool_response(tc), _text_response("100.")])
    cache = TurnToolCache(_POL, [_entry()], {})
    _, _, results = agent.run_turn(list(MESSAGES), SESSION_ID, _tool_response(tc), tool_cache=cache)
    gateway.execute.assert_not_called()
    assert results[0].result_text.startswith("(stored result")


def test_run_turn_miss_executes_and_records():
    tc = _tool_call()
    live = ToolResult(tool_use_id="tu_abc", tool_name="get_balance", result={}, success=True,
                      result_text='{"balance": 5}', projected=True)
    agent, _, _, gateway, _ = _make_manager([_tool_response(tc), _text_response()], tool_result=live)
    cache = TurnToolCache(_POL, [], {})
    agent.run_turn(list(MESSAGES), SESSION_ID, _tool_response(tc), tool_cache=cache)
    gateway.execute.assert_called_once()
    assert cache.drain_batch()["puts"][0]["data"] == {"balance": 5}


def test_run_turn_routes_remember_to_handler():
    tc = ToolCall(tool_name="remember", tool_use_id="tu_r", input_params={"field": "f", "value": "v"})
    agent, _, _, gateway, _ = _make_manager([_tool_response(tc), _text_response()])
    seen = []

    def handler(call, messages):
        seen.append(call.tool_use_id)
        return ToolResult(tool_use_id=call.tool_use_id, tool_name="remember", result={}, success=True,
                          result_text="Saved f.")

    agent.run_turn(list(MESSAGES), SESSION_ID, _tool_response(tc),
                   remember_name="remember", remember_handler=handler)
    assert seen == ["tu_r"]
    gateway.execute.assert_not_called()


def test_build_system_prompt_renders_known_facts():
    agent = _make_manager_for_prompt()
    prompt = agent.build_system_prompt("persona", "", "english", "web", {}, known_facts="- t — x")
    assert "<known_facts>" in _flat(prompt) and "- t — x" in _flat(prompt)
    assert "<known_facts>" not in _flat(agent.build_system_prompt("persona", "", "english", "web", {}))
