from dev_kit.agent.prompts.phases import get_phase_addition


def test_tools_phase_mentions_invocation_rules():
    # GH-137: tools phase now frames around invocation_rules 6-field schema
    result = get_phase_addition("tools")
    assert "invocation_rules" in result


def test_tools_phase_mentions_connector_categories():
    # GH-137: tools phase discusses connector categories (read/write/identity/internal)
    result = get_phase_addition("tools")
    assert "connectors" in result.lower()


def test_tools_phase_includes_connectors_template():
    # GH-137: tools phase embeds agent_core.connectors template, not action_gateway
    result = get_phase_addition("tools")
    assert "connectors" in result


def test_reach_phase_mentions_channels():
    # GH-137: reach phase now describes channel adapters at a pedagogy level
    result = get_phase_addition("reach")
    assert "channel" in result.lower()


def test_reach_phase_mentions_voice_adapter():
    result = get_phase_addition("reach")
    assert "voice" in result.lower()


def test_reach_phase_mentions_web_ui():
    result = get_phase_addition("reach")
    assert "web" in result.lower()


def test_reach_phase_includes_reach_layer_template():
    result = get_phase_addition("reach")
    # reach_layer template is still embedded
    assert "reach_layer" in result or "channels" in result.lower()


def test_reach_phase_no_stale_flat_ui_path():
    result = get_phase_addition("reach")
    # Old path was section=`ui` — must not appear as a standalone instruction
    assert 'section=`ui`' not in result


def test_review_phase_updated_reach_fields():
    result = get_phase_addition("review")
    # Old: 'ui.app_name, ui.app_icon' — must now reference channels structure
    assert "channels" in result
