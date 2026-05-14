"""Tests for field_status.json read/write."""
import json
from pathlib import Path

import pytest

from dev_kit.agent.field_status import (
    FIELD_STATUS_VALUES,
    load_field_status,
    save_field_status,
)


def test_status_set_complete():
    assert FIELD_STATUS_VALUES == {"pending", "answered", "needs_re_asking", "not_applicable"}


def test_save_load_roundtrip(tmp_path: Path):
    status = {"agent_core.foo": "pending", "trust_layer.bar": "answered"}
    p = tmp_path / "field_status.json"
    save_field_status(p, status)
    loaded = load_field_status(p)
    assert loaded == status


def test_load_missing_returns_empty(tmp_path: Path):
    loaded = load_field_status(tmp_path / "missing.json")
    assert loaded == {}


def test_save_validates_status_values(tmp_path: Path):
    p = tmp_path / "field_status.json"
    with pytest.raises(ValueError):
        save_field_status(p, {"foo.bar": "wrong_status"})


def test_load_corrupt_json_raises_value_error(tmp_path: Path):
    """Corrupt JSON file must raise ValueError so callers can propagate as HTTP 500."""
    p = tmp_path / "field_status.json"
    p.write_text("{not valid json")
    with pytest.raises(ValueError, match="Corrupt JSON in field_status file"):
        load_field_status(p)


def test_load_valid_json_non_dict_returns_empty(tmp_path: Path):
    """Valid JSON that is not a dict should return empty dict."""
    p = tmp_path / "field_status.json"
    p.write_text(json.dumps(["pending", "answered"]))
    loaded = load_field_status(p)
    assert loaded == {}
