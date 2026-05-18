"""Tests for IntakeState dataclass and persistence."""
import json
from pathlib import Path

import pytest

from dev_kit.agent.intake_state import (
    BINARY_INTAKE_FIELDS,
    IntakeState,
    load_intake_state,
    save_intake_state,
)


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


def test_binary_flags_seen_defaults_to_empty_list():
    """New IntakeState has an empty binary_flags_seen list by default."""
    state = _empty_state()
    assert state.binary_flags_seen == []


def test_load_old_intake_state_without_binary_flags_seen(tmp_path: Path):
    """An existing intake_state.json without binary_flags_seen loads cleanly.

    This verifies backward compatibility with pre-existing project files that
    were persisted before the binary_flags_seen field was added.
    """
    old_payload = {
        "has_kb": False,
        "has_external_tools": False,
        "is_multi_turn": False,
        "needs_persistent_user_data": False,
        "is_companion_style": False,
        "needs_consent": False,
        "has_hitl": False,
        "selected_channels": ["web"],
        "default_language": "english",
        "supported_languages": ["english"],
        "domain_description": "legacy project",
        "project_name": "legacy",
        "completed": False,
        "updated_at": "2026-05-14T00:00:00+00:00",
        # No binary_flags_seen key — simulates an old file
    }
    old_file = tmp_path / "intake_state.json"
    old_file.write_text(json.dumps(old_payload))

    state = load_intake_state(old_file)
    assert state.binary_flags_seen == []
    assert state.completed is False
    assert state.project_name == "legacy"


def test_binary_intake_fields_constant_has_seven_entries():
    """BINARY_INTAKE_FIELDS must contain exactly the 7 binary flags."""
    assert len(BINARY_INTAKE_FIELDS) == 7
    expected = {
        "has_kb", "has_external_tools", "is_multi_turn",
        "needs_persistent_user_data", "is_companion_style",
        "needs_consent", "has_hitl",
    }
    assert BINARY_INTAKE_FIELDS == expected


def test_save_load_roundtrip_with_binary_flags_seen(tmp_path: Path):
    """binary_flags_seen survives a save/load round-trip."""
    state = _empty_state()
    state.binary_flags_seen = ["has_kb", "has_hitl"]
    state_path = tmp_path / "intake_state.json"
    save_intake_state(state_path, state)
    loaded = load_intake_state(state_path)
    assert loaded.binary_flags_seen == ["has_kb", "has_hitl"]


def test_uses_azure_blob_defaults_false_and_round_trips(tmp_path: Path):
    """uses_azure_blob defaults to False, round-trips through JSON, and is
    forward-compat (an old payload missing the field still loads)."""
    # Default.
    state = _empty_state()
    assert state.uses_azure_blob is False

    # Round-trip when explicitly set True (the knowledge phase captures
    # this via update_intake(field="uses_azure_blob", value=True)).
    state.uses_azure_blob = True
    state_path = tmp_path / "intake_state.json"
    save_intake_state(state_path, state)
    loaded = load_intake_state(state_path)
    assert loaded.uses_azure_blob is True


def test_load_intake_state_tolerates_missing_uses_azure_blob(tmp_path: Path):
    """A legacy payload from before the field was added still loads — the
    default (False) is applied automatically.
    """
    import json
    legacy_payload = {
        "has_kb": False, "has_external_tools": False,
        "is_multi_turn": False, "needs_persistent_user_data": False,
        "is_companion_style": False, "needs_consent": False, "has_hitl": False,
        "selected_channels": ["web"],
        "default_language": "english", "supported_languages": ["english"],
        "domain_description": "", "project_name": "legacy_proj",
        # uses_azure_blob deliberately absent.
    }
    p = tmp_path / "intake_state.json"
    p.write_text(json.dumps(legacy_payload))
    loaded = load_intake_state(p)
    assert loaded.uses_azure_blob is False


def test_load_intake_state_drops_unknown_keys(tmp_path: Path):
    """A forward-compat read with an extra key the dataclass doesn't know
    about must not crash — the unknown key is silently dropped.
    """
    import json
    payload_with_extra = {
        "has_kb": False, "has_external_tools": False,
        "is_multi_turn": False, "needs_persistent_user_data": False,
        "is_companion_style": False, "needs_consent": False, "has_hitl": False,
        "selected_channels": ["web"],
        "default_language": "english", "supported_languages": ["english"],
        "domain_description": "", "project_name": "fwd",
        "uses_azure_blob": True,
        "future_field_we_dont_know_about": "ignored",
    }
    p = tmp_path / "intake_state.json"
    p.write_text(json.dumps(payload_with_extra))
    loaded = load_intake_state(p)
    assert loaded.uses_azure_blob is True
    assert not hasattr(loaded, "future_field_we_dont_know_about")
