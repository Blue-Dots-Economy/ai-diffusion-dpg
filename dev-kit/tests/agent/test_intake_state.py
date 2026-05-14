"""Tests for IntakeState dataclass and persistence."""
import json
import tempfile
from pathlib import Path

import pytest

from dev_kit.agent.intake_state import IntakeState, load_intake_state, save_intake_state


def _empty_state() -> IntakeState:
    return IntakeState(
        has_kb=False,
        has_external_tools=False,
        is_multi_turn=False,
        needs_persistent_user_data=False,
        is_companion_style=False,
        needs_consent=False,
        has_hitl=False,
        selected_channels=["web"],
        default_language="english",
        supported_languages=["english"],
        domain_description="",
        project_name="",
    )


def test_intake_state_has_twelve_fields_plus_bookkeeping():
    state = _empty_state()
    # 12 intake fields + completed + updated_at
    assert hasattr(state, "has_kb")
    assert hasattr(state, "completed")
    assert hasattr(state, "updated_at")
    assert state.completed is False
    assert state.updated_at == ""


def test_save_load_roundtrip(tmp_path: Path):
    state = _empty_state()
    state_path = tmp_path / "intake_state.json"
    save_intake_state(state_path, state)
    loaded = load_intake_state(state_path)
    assert loaded == state


def test_load_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_intake_state(tmp_path / "does_not_exist.json")


def test_selected_channels_only_web_or_voice():
    """Channel literal forbids cli; web+voice only."""
    with pytest.raises(ValueError):
        IntakeState(
            has_kb=False, has_external_tools=False,
            is_multi_turn=False, needs_persistent_user_data=False, is_companion_style=False,
            needs_consent=False, has_hitl=False,
            selected_channels=["cli"],   # invalid
            default_language="english", supported_languages=["english"],
            domain_description="", project_name="",
        )
