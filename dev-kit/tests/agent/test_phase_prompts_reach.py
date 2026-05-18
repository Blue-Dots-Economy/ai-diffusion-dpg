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


def test_reach_prompt_covers_voice_tts_rules_terminal_word_and_filler() -> None:
    """Voice TTS / terminal_word / filler_phrase live in the reach phase
    per FIELD_RULES. The reach prompt must cover all three explicitly with
    the correct paths — they were previously buried in the language
    prompt, leading to write failures (`validation_unknown_section`) and
    a phase that never advanced.
    """
    result = build([], "", "", _intake(selected_channels=["web", "voice"]))

    # TTS rules — at least one rule key plus the correct config path.
    assert "tts_rules" in result
    assert "agent_core, section=channels.voice.tts_rules" in result
    # Each canonical TTS rule key is named so the LLM knows what to propose.
    for key in (
        "numbers", "money", "dates", "time", "phone",
        "abbreviations", "output_script", "english_loanwords",
    ):
        assert key in result

    # Terminal word + filler phrase
    assert "terminal_word" in result
    assert "filler_phrase" in result
    assert "filler_threshold_ms" in result
    # The reach-layer config path for terminal_word et al.
    assert "block=reach_layer, section=channels.voice" in result


def test_reach_voice_section_absent_when_voice_not_selected():
    result = build([], "", "", _intake(selected_channels=["web"]))
    # Should note that voice is not selected
    assert "Not selected" in result or "skip" in result.lower()


def test_reach_prompt_bans_channel_re_ask() -> None:
    """Reach phase MUST NOT ask the user "will it run on web?" / "will it
    run on voice?" — `selected_channels` is captured at project creation.

    GoGuide regression: the LLM produced exactly:
        "Will it operate on web (chat interface in a browser or app)?
         Will it operate on voice (phone call or voice app)?"
    despite the existing soft "do not ask" line. The fix surfaces
    `selected_channels` as an already-set value at the top of the prompt
    AND shows the wrong-shape phrasing as a worked anti-pattern.
    """
    result = build([], "", "", _intake(selected_channels=["web", "voice"]))

    # selected_channels surfaces as already-set, matching the language
    # prompt's already-set block format.
    assert "Already set on the project-creation form" in result
    assert "`selected_channels` = " in result
    assert "['web', 'voice']" in result

    # The two literal wrong-shape lines must appear in the prompt so the
    # LLM has a concrete anti-pattern to match against.
    assert "Will it operate on web" in result
    assert "Will it operate on voice" in result

    # And a hard "NEVER ask" instruction must be present (capitalized
    # "NEVER" — soft "Do NOT ask" wording has already failed in practice).
    assert "NEVER ask" in result


def test_language_prompt_also_surfaces_selected_channels_as_already_set() -> None:
    """The language phase is where `agent_core.channels.*` gets configured,
    so it must also surface `selected_channels` as already-set and ban
    the channel re-ask — the LLM should NOT pivot from "configure
    channels.<name>" to "which channels do you want?".
    """
    from dev_kit.agent.phase_prompts.language import build as language_build

    result = language_build(
        [], "", "", _intake(selected_channels=["web", "voice"])
    )
    assert "`selected_channels` = " in result
    assert "['web', 'voice']" in result
    assert "NEVER ask" in result
