"""Tests for dev_kit.agent.phase_prompts.tier."""
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
    # tier phase fields are typically in IntakeState, not FIELD_RULES,
    # but we allow them for robustness
    rule = FieldRule(category="chat", phase="tier", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.tier import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: Tier intake chat" in result


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
        _fake_field("intake_state.has_kb", "Whether the agent needs a KB"),
        _fake_field("intake_state.has_hitl", "Whether HITL escalation is needed"),
    ]
    result = build(fields, "", "", _intake())
    assert "intake_state.has_kb" in result
    assert "Whether the agent needs a KB" in result
    assert "intake_state.has_hitl" in result
    assert "Whether HITL escalation is needed" in result


def test_tier_contains_4_turn_instructions():
    result = build([], "", "", _intake())
    assert "Turn 1" in result
    assert "Turn 2" in result
    assert "Turn 3" in result
    assert "Turn 4" in result


def test_tier_does_not_ask_form_fields():
    """Form-captured fields must not appear as questions in the tier prompt.

    The LLM must NOT be instructed to ask the user for any of the 5 form
    fields (project_name, domain_description, selected_channels,
    default_language, supported_languages) — those are pre-populated
    server-side before the tier phase begins.
    """
    result = build([], "", "", _intake(
        project_name="my_project",
        domain_description="A farming advisor",
    ))
    result_lower = result.lower()
    # None of these imperative phrasings should appear in the output
    assert "what is your project name" not in result_lower
    assert "what is your domain" not in result_lower
    assert "please provide your project name" not in result_lower
    # domain_description value appears as a reference, not a question
    assert "A farming advisor" in result


def test_tier_has_update_intake_calls():
    result = build([], "", "", _intake())
    assert "update_intake" in result
    assert "has_kb" in result
    assert "has_external_tools" in result
    assert "is_multi_turn" in result
    assert "needs_consent" in result
    assert "has_hitl" in result
