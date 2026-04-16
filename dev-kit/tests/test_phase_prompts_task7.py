from dev_kit.agent.prompts.phases import get_phase_addition


def test_tools_phase_has_always_ask_for_more():
    result = get_phase_addition("tools")
    assert "Are there any other tools" in result or "any other tools" in result.lower()


def test_tools_phase_has_all_three_paths():
    result = get_phase_addition("tools")
    assert "OpenAPI" in result
    assert "MCP" in result or "mcp" in result.lower()
    assert "manual" in result.lower() or "Manual" in result


def test_tools_phase_includes_action_gateway_template():
    result = get_phase_addition("tools")
    # action_gateway template has 'tools:' as a top-level key
    assert "tools:" in result


def test_reach_phase_has_channel_selection_step():
    result = get_phase_addition("reach")
    assert "set_reach_channels" in result


def test_reach_phase_has_web_section_path():
    result = get_phase_addition("reach")
    assert "reach_layer.channels.web.ui" in result


def test_reach_phase_has_cli_section_path():
    result = get_phase_addition("reach")
    assert "reach_layer.channels.cli" in result


def test_reach_phase_has_voice_section_path():
    result = get_phase_addition("reach")
    assert "reach_layer.channels.voice.raya" in result


def test_reach_phase_no_stale_flat_ui_path():
    result = get_phase_addition("reach")
    # Old path was section=`ui` — must not appear as a standalone instruction
    assert 'section=`ui`' not in result


def test_review_phase_updated_reach_fields():
    result = get_phase_addition("review")
    # Old: 'ui.app_name, ui.app_icon' — must now reference channels structure
    assert "channels" in result
