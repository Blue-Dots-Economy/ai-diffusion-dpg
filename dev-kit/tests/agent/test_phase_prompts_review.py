"""Tests for dev_kit.agent.phase_prompts.review."""
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
    """Return a minimal object that quacks like a (path, FieldRule) tuple."""
    from dev_kit.agent.field_rules import FieldRule
    rule = FieldRule(category="chat", phase="review", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.review import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: Review" in result


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
        _fake_field("trust_layer.consent.required", "Whether consent is required"),
    ]
    result = build(fields, "", "", _intake())
    assert "trust_layer.rules.blocked_phrases" in result
    assert "Blocked phrase list" in result
    assert "trust_layer.consent.required" in result
    assert "Whether consent is required" in result


def test_review_mentions_re_asking():
    """The review prompt must explicitly instruct re-asking needs_re_asking fields."""
    result = build([], "", "", _intake())
    result_lower = result.lower()
    assert "needs_re_asking" in result_lower or "re-ask" in result_lower


def test_review_does_not_instruct_set_phase():
    """The review prompt must NOT instruct the LLM to call set_phase(...)."""
    result = build([], "", "", _intake())
    assert "set_phase(" not in result


def test_review_directs_to_deploy_or_validate_config():
    """The review prompt must reference the terminal handoff step (Deploy or validate_config)."""
    result = build([], "", "", _intake())
    assert any(token in result for token in ("Deploy", "validate_config", "complete"))
