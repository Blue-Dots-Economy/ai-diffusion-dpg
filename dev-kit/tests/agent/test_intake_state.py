"""Tests for IntakeState dataclass and persistence."""
import json
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


def test_load_corrupt_json_raises_value_error(tmp_path: Path):
    """Corrupt JSON file causes load_intake_state to raise ValueError with the file path."""
    bad_file = tmp_path / "intake_state.json"
    bad_file.write_text("{not valid json{{")
    with pytest.raises(ValueError, match=str(bad_file)):
        load_intake_state(bad_file)


def test_load_schema_mismatch_raises_value_error(tmp_path: Path):
    """JSON with a missing required field causes load_intake_state to raise ValueError."""
    bad_file = tmp_path / "intake_state.json"
    # Write JSON that is missing 'has_kb' (a required field)
    payload = {
        "has_external_tools": False,
        "is_multi_turn": False,
        "needs_persistent_user_data": False,
        "is_companion_style": False,
        "needs_consent": False,
        "has_hitl": False,
        "selected_channels": ["web"],
        "default_language": "english",
        "supported_languages": ["english"],
        "domain_description": "",
        "project_name": "",
    }
    bad_file.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_intake_state(bad_file)


def test_selected_channels_empty_rejected():
    """An empty selected_channels list must raise ValueError."""
    with pytest.raises(ValueError, match="selected_channels must be non-empty"):
        IntakeState(
            has_kb=False, has_external_tools=False,
            is_multi_turn=False, needs_persistent_user_data=False, is_companion_style=False,
            needs_consent=False, has_hitl=False,
            selected_channels=[],   # empty — must be rejected
            default_language="english", supported_languages=["english"],
            domain_description="", project_name="",
        )
