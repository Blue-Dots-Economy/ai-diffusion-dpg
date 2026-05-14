"""Tests for dev_kit.agent.phase_prompts.workflow."""
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
    rule = FieldRule(category="chat", phase="workflow", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.workflow import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: Workflow" in result


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
        _fake_field("agent_core.agent_workflow.workflow_id", "Workflow identifier"),
        _fake_field("agent_core.agent_workflow.agent_system_prompt", "Top-level persona"),
    ]
    result = build(fields, "", "", _intake())
    assert "agent_core.agent_workflow.workflow_id" in result
    assert "Workflow identifier" in result
    assert "agent_core.agent_workflow.agent_system_prompt" in result
    assert "Top-level persona" in result


def test_workflow_kb_note_when_has_kb():
    result = build([], "", "", _intake(has_kb=True))
    assert "knowledge_retrieval" in result
    assert "global_tools" in result


def test_workflow_no_kb_note_when_no_kb():
    result = build([], "", "", _intake(has_kb=False))
    # global_tools is mentioned in the hard rules but KB-specific pre-check
    # block should not appear
    assert "connectors.internal" not in result or "Pre-check" not in result
