"""Tests for dev_kit.agent.phase_prompts.memory."""
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
    rule = FieldRule(category="chat", phase="memory", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.memory import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: Memory" in result


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
        _fake_field("memory_layer.state.session.ttl_minutes", "Session TTL in minutes"),
        _fake_field("memory_layer.state.session.schema", "Session state schema fields"),
    ]
    result = build(fields, "", "", _intake())
    assert "memory_layer.state.session.ttl_minutes" in result
    assert "Session TTL in minutes" in result
    assert "memory_layer.state.session.schema" in result
    assert "Session state schema fields" in result


def test_memory_persistent_note_when_needed():
    result = build([], "", "", _intake(needs_persistent_user_data=True))
    assert "saved" in result
    assert "needs_persistent_user_data" in result or "cross-session" in result


def test_memory_anonymous_note_when_not_needed():
    result = build([], "", "", _intake(needs_persistent_user_data=False))
    assert "anonymous" in result
