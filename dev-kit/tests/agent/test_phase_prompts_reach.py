"""Tests for dev_kit.agent.phase_prompts.reach."""
from __future__ import annotations


def _intake(**overrides):
    base = dict(
        has_kb=False, has_external_tools=False, is_multi_turn=False,
        needs_persistent_user_data=False, is_companion_style=False,
        needs_consent=False, has_hitl=False,
        selected_channels=["web"], default_language="en", supported_languages=["en"],
        domain_description="test", project_name="test_project",
    )
    base.update(overrides)
    from dev_kit.agent.intake_state import IntakeState
    return IntakeState(**base)


def _fake_field(path: str, description: str = "A field"):
    from dev_kit.agent.field_rules import FieldRule
    rule = FieldRule(category="chat", phase="reach", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.reach import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: Reach" in result


def test_build_contains_field_section():
    result = build([], "", "", _intake())
    assert "## Fields to capture this phase" in result


def test_build_contains_pydantic_schema_section():
    result = build([], "", "", _intake())
    assert "## Pydantic schemas" in result


def test_build_injects_pydantic_schemas_param():
    result = build([], "class FooSection(BaseModel): pass", "", _intake())
    assert "class FooSection(BaseModel): pass" in result


def test_build_injects_cross_phase_refs_param():
    result = build([], "", "preset_value=xyz", _intake())
    assert "preset_value=xyz" in result


def test_build_renders_pending_fields():
    fields = [
        _fake_field("reach_layer.channels.web.ui.app_name", "Web app name"),
        _fake_field("reach_layer.channels.voice.raya.voice_id", "Raya voice ID"),
    ]
    result = build(fields, "", "", _intake())
    assert "reach_layer.channels.web.ui.app_name" in result
    assert "Web app name" in result
    assert "reach_layer.channels.voice.raya.voice_id" in result
    assert "Raya voice ID" in result


def test_reach_voice_section_present_when_voice_selected():
    result = build([], "", "", _intake(selected_channels=["web", "voice"]))
    assert "Raya" in result
    assert "voice_id" in result


def test_reach_voice_section_absent_when_voice_not_selected():
    result = build([], "", "", _intake(selected_channels=["web"]))
    # Should note that voice is not selected
    assert "Not selected" in result or "skip" in result.lower()
