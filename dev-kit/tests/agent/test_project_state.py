"""Tests for project_state: load/save accumulator dict."""
from pathlib import Path

import pytest

from dev_kit.agent.project_state import (
    BLOCKS,
    empty_accumulator,
    load_accumulator,
    save_accumulator,
)


def test_blocks_constant_has_seven_entries():
    assert set(BLOCKS) == {
        "agent_core", "trust_layer", "knowledge_engine", "memory_layer",
        "action_gateway", "reach_layer", "observability_layer",
    }


def test_empty_accumulator_has_all_blocks():
    acc = empty_accumulator()
    assert set(acc.keys()) == set(BLOCKS)
    for block in BLOCKS:
        assert acc[block] == {}


def test_save_load_roundtrip(tmp_path: Path):
    acc = empty_accumulator()
    acc["agent_core"]["agent"] = {"primary_model": "claude-sonnet-4-5"}
    acc["trust_layer"]["trust"] = {"policy_pack": "kkb_advisory_jobs"}
    p = tmp_path / "accumulator.json"
    save_accumulator(p, acc)
    loaded = load_accumulator(p)
    assert loaded == acc


def test_load_missing_returns_empty(tmp_path: Path):
    """Missing file → fresh empty accumulator (not an error)."""
    acc = load_accumulator(tmp_path / "missing.json")
    assert acc == empty_accumulator()


def test_load_corrupt_json_raises_value_error(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("not valid json {{{")
    with pytest.raises(ValueError, match="Corrupt"):
        load_accumulator(p)


def test_load_unknown_block_raises_value_error(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text('{"unknown_block": {}}')
    with pytest.raises(ValueError, match="unknown block"):
        load_accumulator(p)
