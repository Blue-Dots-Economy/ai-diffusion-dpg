"""Tests for dev_kit.agent.prompts.phases."""
import pytest
from dev_kit.agent.prompts.phases import get_phase_addition


class TestGetPhaseAddition:
    """Test suite for get_phase_addition function."""

    def test_overview_phase_returns_guidance(self):
        """Overview phase should return non-empty guidance text."""
        result = get_phase_addition("overview")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "overview" in result.lower() or "Overview" in result

    def test_language_phase_returns_non_empty_string(self):
        """Language phase should return non-empty schema context."""
        result = get_phase_addition("language")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Language" in result or "language" in result.lower()

    def test_knowledge_phase_returns_non_empty_string(self):
        """Knowledge phase should return non-empty schema context."""
        result = get_phase_addition("knowledge")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Knowledge" in result or "knowledge" in result.lower()

    def test_memory_phase_returns_non_empty_string(self):
        """Memory phase should return non-empty schema context."""
        result = get_phase_addition("memory")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Memory" in result or "memory" in result.lower()

    def test_trust_phase_returns_non_empty_string(self):
        """Trust phase should return non-empty schema context."""
        result = get_phase_addition("trust")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Trust" in result or "trust" in result.lower()

    def test_tools_phase_returns_non_empty_string(self):
        """Tools phase should return non-empty schema context."""
        result = get_phase_addition("tools")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Tools" in result or "tools" in result.lower()

    def test_workflow_phase_returns_non_empty_string(self):
        """Workflow phase should return non-empty schema context."""
        result = get_phase_addition("workflow")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Workflow" in result or "workflow" in result.lower()

    def test_review_phase_returns_non_empty_string(self):
        """Review phase should return non-empty schema context."""
        result = get_phase_addition("review")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Review" in result or "review" in result.lower()

    def test_unknown_phase_returns_empty_string(self):
        """Unknown phase name should return empty string, not raise."""
        result = get_phase_addition("unknown_phase")
        assert result == ""

    def test_workflow_with_available_tools_includes_tool_names(self):
        """Workflow phase with available_tools should include tool names in output."""
        tools = ["crm_api", "sms_service"]
        result = get_phase_addition("workflow", available_tools=tools)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "crm_api" in result
        assert "sms_service" in result

    def test_workflow_with_none_tools_does_not_raise(self):
        """Workflow phase with available_tools=None should not raise."""
        result = get_phase_addition("workflow", available_tools=None)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_workflow_with_empty_tools_list(self):
        """Workflow phase with empty tools list should return base workflow context."""
        result = get_phase_addition("workflow", available_tools=[])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_phase_names_are_case_sensitive(self):
        """Phase names should be case-sensitive; 'Language' (capital L) should return empty."""
        result = get_phase_addition("Language")
        assert result == ""

    def test_all_phase_names_case_sensitive(self):
        """Verify case sensitivity for all known phase names."""
        known_phases = ["overview", "language", "knowledge", "memory", "trust", "tools", "workflow", "review"]
        for phase in known_phases:
            capitalized = phase.capitalize()
            result = get_phase_addition(capitalized)
            assert result == "", f"Expected empty string for capitalized phase '{capitalized}', got: {result!r}"

    def test_return_type_is_always_string(self):
        """Return type should always be a string."""
        test_phases = ["overview", "language", "memory", "unknown", "workflow"]
        for phase in test_phases:
            result = get_phase_addition(phase)
            assert isinstance(result, str), f"Expected str for phase '{phase}', got {type(result)}"

    def test_workflow_with_multiple_tools_in_output(self):
        """Workflow phase should include all tools in the output."""
        tools = ["api_1", "api_2", "api_3"]
        result = get_phase_addition("workflow", available_tools=tools)
        for tool in tools:
            assert tool in result, f"Expected tool '{tool}' in workflow output"

    def test_language_phase_includes_schema_header(self):
        """Language phase should include a schema context header."""
        result = get_phase_addition("language")
        assert "Schema context" in result or "Language" in result

    def test_knowledge_phase_includes_glossary_format(self):
        """Knowledge phase should mention glossary format."""
        result = get_phase_addition("knowledge")
        assert "Glossary" in result or "glossary" in result.lower()

    def test_memory_phase_includes_session_schema(self):
        """Memory phase should mention session schema."""
        result = get_phase_addition("memory")
        assert "Session" in result or "session" in result.lower()

    def test_user_state_phase_returns_non_empty(self):
        """User state phase should return non-empty text."""
        text = get_phase_addition("user_state")
        assert text
        assert "user_state_model" in text
        assert "Conversational" in text

    def test_user_state_phase_mentions_schema_fields(self):
        """User state phase should mention schema fields."""
        text = get_phase_addition("user_state")
        assert "default_state" in text
        assert "states" in text
        assert "signals" in text
        assert "guidance" in text

    def test_user_state_phase_mentions_threshold_location(self):
        """User state phase should mention threshold location."""
        text = get_phase_addition("user_state")
        assert "user_state_confidence_threshold" in text
        assert "preprocessing.nlu_processor" in text

    def test_overview_phase_lists_user_state_in_sequence(self):
        """Overview phase should list user_state in correct sequence."""
        text = get_phase_addition("overview")
        assert "user_state" in text
        assert text.index("memory") < text.index("user_state") < text.index("trust")


def test_tier_phase_returns_decision_tree():
    text = get_phase_addition("tier")
    assert "Q1" in text and "Q2" in text and "Q3" in text and "Q4" in text
    assert "Transactional" in text
    assert "Informational" in text
    assert "Agentic" in text
    assert "Conversational" in text
    assert "set_agent_type" in text


def test_overview_phase_mentions_tier_as_first():
    text = get_phase_addition("overview")
    assert "tier" in text.lower()


def test_language_phase_mentions_tts_rules():
    text = get_phase_addition("language")
    assert "TTS" in text or "tts_rules" in text


def test_language_phase_mentions_terminal_word_for_voice():
    text = get_phase_addition("language")
    assert "terminal_word" in text or "terminal word" in text.lower()


def test_knowledge_phase_per_type_hint():
    text = get_phase_addition("knowledge")
    assert "Informational" in text
    assert "Transactional" in text or "skip" in text.lower()


def test_memory_phase_mentions_contact_memory_states():
    text = get_phase_addition("memory")
    for s in ["new", "sparse", "rich", "mid-journey", "post-application"]:
        assert s in text.lower() or s.replace("-", "_") in text.lower()


def test_user_state_phase_mentions_guide_section_and_threshold():
    text = get_phase_addition("user_state")
    assert "§2.5" in text or "Conversation State Model" in text
    assert "user_state_confidence_threshold" in text


def test_trust_phase_dignity_check_questions_present():
    text = get_phase_addition("trust")
    for q in ["blame", "over-promise", "urgency", "agency", "script"]:
        assert q in text.lower()


def test_tools_phase_lists_six_invocation_rule_fields():
    text = get_phase_addition("tools")
    for f in ["call_when", "required_before_calling", "must_not_substitute",
              "on_empty", "on_failure", "bridge_line"]:
        assert f in text


def test_workflow_phase_mentions_opening_phrase():
    text = get_phase_addition("workflow")
    assert "opening_phrase" in text
    assert "is_start" in text


def test_reach_phase_mentions_consolidated_channels_note():
    text = get_phase_addition("reach")
    assert "agent_core.channels" in text or "channels.voice" in text


def test_review_phase_mentions_validate_config():
    text = get_phase_addition("review")
    assert "validate_config" in text
