"""Each phase prompt must inject the relevant section schemas as Pydantic source code."""
from dev_kit.agent.prompts.phases import get_phase_addition


def test_language_phase_includes_agent_section_source():
    text = get_phase_addition("language")
    assert "class AgentSection" in text
    assert "primary_model" in text
    # The constraint must be visible
    assert "ge=1" in text or "le=20" in text


def test_language_phase_includes_preprocessing_section():
    text = get_phase_addition("language")
    assert "class PreprocessingSection" in text or "class NLUProcessorSection" in text


def test_language_phase_includes_conversation_section():
    text = get_phase_addition("language")
    assert "class ConversationSection" in text


def test_knowledge_phase_includes_knowledge_section():
    text = get_phase_addition("knowledge")
    assert "class KnowledgeSection" in text
    assert "intent_filters" in text


def test_workflow_phase_includes_agent_workflow_section():
    text = get_phase_addition("workflow")
    assert "class AgentWorkflowSection" in text
    assert "class SubAgent" in text


def test_trust_phase_includes_dignity_check_section():
    text = get_phase_addition("trust")
    assert "class DignityCheckSection" in text


def test_memory_phase_includes_state_section():
    text = get_phase_addition("memory")
    assert "class StateSection" in text


def test_tools_phase_includes_tools_section():
    text = get_phase_addition("tools")
    assert "class ToolsSection" in text


def test_observability_phase_includes_outcomes():
    text = get_phase_addition("observability")
    assert "class OutcomesConfig" in text or "class ObservabilitySection" in text


def test_reach_phase_includes_reach_layer_section():
    text = get_phase_addition("reach")
    assert "class ReachLayerSection" in text or "class WebChannelSection" in text
