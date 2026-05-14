"""Tests for dev_kit.agent.phase_prompts.language."""
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
    rule = FieldRule(category="chat", phase="language", description=description)
    return (path, rule)


from dev_kit.agent.phase_prompts.language import build


def test_build_returns_nonempty_string():
    result = build([], "", "", _intake())
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_contains_phase_header():
    result = build([], "", "", _intake())
    assert "# Phase: Language" in result


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
        _fake_field("agent_core.agent.primary_model", "Primary LLM model ID"),
        _fake_field("agent_core.preprocessing.nlu_processor.intents", "NLU intent list"),
    ]
    result = build(fields, "", "", _intake())
    assert "agent_core.agent.primary_model" in result
    assert "Primary LLM model ID" in result
    assert "agent_core.preprocessing.nlu_processor.intents" in result
    assert "NLU intent list" in result


def test_language_voice_tts_section_when_voice_selected():
    result = build([], "", "", _intake(selected_channels=["web", "voice"]))
    assert "tts_rules" in result or "TTS" in result
    assert "terminal_word" in result


def test_language_voice_section_absent_when_web_only():
    result = build([], "", "", _intake(selected_channels=["web"]))
    assert "Not in selected_channels" in result or "skip ALL voice" in result.lower()


def test_language_multilingual_note():
    result = build([], "", "", _intake(supported_languages=["en", "hi"]))
    assert "Multilingual" in result or "multilingual" in result
