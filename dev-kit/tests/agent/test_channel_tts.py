"""Tests for dev_kit.agent.channel_tts.

Belongs to the dev-kit deterministic wizard. Pure dict-transformation tests
for merge_voice_tts_into_suffix / strip_voice_tts_from_suffix, plus
integration tests that drive these through render_all and load_block_from_file.
"""
from __future__ import annotations

import yaml

from dev_kit.agent.channel_tts import (
    TTS_BLOCK_BEGIN,
    TTS_BLOCK_END,
    merge_voice_tts_into_suffix,
    strip_voice_tts_from_suffix,
)
from dev_kit.agent.intake_state import IntakeState
from dev_kit.agent.project_state import empty_accumulator
from dev_kit.agent.renderer import load_block_from_file, render_all


def _intake(**overrides) -> IntakeState:
    """Build a minimal IntakeState for the integration tests."""
    defaults = dict(
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
        selected_channels=["web", "voice"],
        default_language="english",
        supported_languages=["english"],
        domain_description="Test",
        project_name="testproj",
    )
    defaults.update(overrides)
    return IntakeState(**defaults)


def _voice_rules() -> dict:
    return {
        "numbers": "Write all numbers in words in Devanagari.",
        "money": "Speak amounts in full words.",
        "dates": "",  # empty — must be skipped
        "time": "Use सुबह/दोपहर/शाम/रात instead of AM/PM.",
        "phone": None,  # non-string — must be skipped
        "abbreviations": "Expand as spoken letters.",
        "output_script": "Hindi in Devanagari only.",
        "english_loanwords": "Transliterate into Devanagari.",
    }


def _voice_config(suffix: str = "", rules: dict | None = None) -> dict:
    voice: dict = {"system_prompt_suffix": suffix, "terminal_word": "Goodbye"}
    voice["tts_rules"] = rules if rules is not None else _voice_rules()
    return {"channels": {"voice": voice}}


class TestMergeVoiceTtsIntoSuffix:
    def test_empty_rules_leaves_suffix_unchanged(self):
        cfg = _voice_config(suffix="short and precise.", rules={k: "" for k in _voice_rules()})
        merged = merge_voice_tts_into_suffix(cfg)
        assert merged["channels"]["voice"]["system_prompt_suffix"] == "short and precise."

    def test_null_tts_rules_is_noop(self):
        cfg = {"channels": {"voice": {"system_prompt_suffix": "x", "tts_rules": None}}}
        merged = merge_voice_tts_into_suffix(cfg)
        assert merged["channels"]["voice"]["system_prompt_suffix"] == "x"

    def test_missing_channels_key_is_noop(self):
        cfg = {"agent": {"primary_model": "claude-haiku-4-5"}}
        assert merge_voice_tts_into_suffix(cfg) == cfg

    def test_appends_delimited_block_after_existing_prefix(self):
        cfg = _voice_config(suffix="Keep it short.")
        merged = merge_voice_tts_into_suffix(cfg)
        suffix = merged["channels"]["voice"]["system_prompt_suffix"]
        assert suffix.startswith("Keep it short.")
        assert TTS_BLOCK_BEGIN in suffix
        assert TTS_BLOCK_END in suffix
        assert "- Numbers: Write all numbers in words in Devanagari." in suffix
        # Empty / non-string rules are skipped.
        assert "- Dates:" not in suffix
        assert "- Phone numbers:" not in suffix

    def test_block_only_when_no_author_prefix(self):
        cfg = _voice_config(suffix="")
        merged = merge_voice_tts_into_suffix(cfg)
        suffix = merged["channels"]["voice"]["system_prompt_suffix"]
        assert suffix.startswith(TTS_BLOCK_BEGIN)
        assert suffix.endswith(TTS_BLOCK_END)

    def test_idempotent_repeat_render(self):
        cfg = _voice_config(suffix="Keep it short.")
        once = merge_voice_tts_into_suffix(cfg)
        twice = merge_voice_tts_into_suffix(once)
        assert (
            twice["channels"]["voice"]["system_prompt_suffix"]
            == once["channels"]["voice"]["system_prompt_suffix"]
        )

    def test_does_not_mutate_input(self):
        cfg = _voice_config(suffix="Keep it short.")
        original = yaml.safe_dump(cfg)
        merge_voice_tts_into_suffix(cfg)
        assert yaml.safe_dump(cfg) == original

    def test_tts_rules_field_retained_in_output(self):
        cfg = _voice_config(suffix="Keep it short.")
        merged = merge_voice_tts_into_suffix(cfg)
        assert merged["channels"]["voice"]["tts_rules"] == cfg["channels"]["voice"]["tts_rules"]


class TestStripVoiceTtsFromSuffix:
    def test_removes_rendered_block(self):
        cfg = merge_voice_tts_into_suffix(_voice_config(suffix="Keep it short."))
        stripped = strip_voice_tts_from_suffix(cfg)
        assert stripped["channels"]["voice"]["system_prompt_suffix"] == "Keep it short."

    def test_no_markers_no_change(self):
        cfg = _voice_config(suffix="Keep it short.")
        assert strip_voice_tts_from_suffix(cfg) == cfg

    def test_missing_voice_channel_is_noop(self):
        cfg = {"channels": {"chat": {"system_prompt_suffix": "x"}}}
        assert strip_voice_tts_from_suffix(cfg) == cfg


class TestRendererIntegration:
    """Drive merge / strip behaviour through render_all and load_block_from_file."""

    def test_render_all_merges_voice_tts(self, tmp_path):
        acc = empty_accumulator()
        acc["agent_core"] = {
            "channels": {
                "voice": {
                    "system_prompt_suffix": "Keep it short.",
                    "terminal_word": "Goodbye",
                    "tts_rules": {k: "" for k in _voice_rules()}
                    | {"numbers": "Write all numbers in words in Devanagari."},
                }
            }
        }
        render_all(tmp_path, acc, _intake())
        content = (tmp_path / "agent_core.yaml").read_text()
        assert TTS_BLOCK_BEGIN in content
        assert "Numbers: Write all numbers in words in Devanagari." in content
        # Accumulator's in-memory state is untouched — suffix still just prose.
        assert (
            acc["agent_core"]["channels"]["voice"]["system_prompt_suffix"]
            == "Keep it short."
        )

    def test_load_block_strips_rendered_tts_block(self, tmp_path):
        acc = empty_accumulator()
        acc["agent_core"] = {
            "channels": {
                "voice": {
                    "system_prompt_suffix": "Keep it short.",
                    "terminal_word": "Goodbye",
                    "tts_rules": {k: "" for k in _voice_rules()}
                    | {"numbers": "Write all numbers in words in Devanagari."},
                }
            }
        }
        render_all(tmp_path, acc, _intake())
        loaded = load_block_from_file(tmp_path, "agent_core")
        assert loaded["channels"]["voice"]["system_prompt_suffix"] == "Keep it short."
        assert loaded["channels"]["voice"]["tts_rules"]["numbers"].startswith("Write")

    def test_non_agent_core_block_unaffected(self, tmp_path):
        acc = empty_accumulator()
        acc["trust_layer"] = {"trust": {"input_rules": {"blocked_phrases": ["spam"]}}}
        render_all(tmp_path, acc, _intake())
        content = (tmp_path / "trust_layer.yaml").read_text()
        assert TTS_BLOCK_BEGIN not in content
