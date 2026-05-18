"""Tests for dev_kit.agent.phase_prompts.trust."""
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
    rule = FieldRule(category="chat", phase="trust", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.trust import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: Trust" in result


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
        _fake_field("trust_layer.rules.blocked_phrases", "Blocked phrase list"),
        _fake_field("trust_layer.consent.required", "Consent required flag"),
    ]
    result = build(fields, "", "", _intake())
    assert "trust_layer.rules.blocked_phrases" in result
    assert "Blocked phrase list" in result
    assert "trust_layer.consent.required" in result
    assert "Consent required flag" in result


def test_trust_dignity_check_for_companion_style():
    """For companion-style agents the trust prompt must mention the dignity
    check (so the LLM knows it's in place) but MUST NOT instruct the LLM
    to write `enabled` or `questions` — both are predetermined fields set
    by the router cascade from `is_companion_style`. Writing them via
    `update_config` is rejected as a non-chat field.
    """
    result = build([], "", "", _intake(is_companion_style=True))
    assert "dignity_check" in result
    # The prompt must explicitly mark dignity_check as predetermined.
    assert "predetermined" in result.lower() or "router cascade" in result.lower()
    # And must NOT instruct the LLM to write either dignity field via
    # block/section/values.
    assert "section=dignity_check" not in result


def test_trust_no_dignity_check_for_non_companion():
    result = build([], "", "", _intake(is_companion_style=False))
    assert "Not required" in result or "not companion-style" in result.lower()
