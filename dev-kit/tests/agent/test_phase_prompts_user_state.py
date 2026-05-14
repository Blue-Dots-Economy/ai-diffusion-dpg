"""Tests for dev_kit.agent.phase_prompts.user_state."""
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
    rule = FieldRule(category="chat", phase="user_state", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.user_state import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: User State" in result


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
        _fake_field("agent_core.conversation.user_state_model.default_state", "Default user state"),
        _fake_field("agent_core.conversation.user_state_model.states", "User state definitions"),
    ]
    result = build(fields, "", "", _intake())
    assert "agent_core.conversation.user_state_model.default_state" in result
    assert "Default user state" in result
    assert "agent_core.conversation.user_state_model.states" in result
    assert "User state definitions" in result


def test_user_state_required_note_for_companion():
    result = build([], "", "", _intake(is_companion_style=True))
    assert "REQUIRED" in result


def test_user_state_recommended_note_for_multi_turn():
    result = build([], "", "", _intake(is_multi_turn=True))
    assert "recommended" in result.lower()
