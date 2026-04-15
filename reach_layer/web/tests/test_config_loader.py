"""
reach_layer/tests/test_config_loader.py

Tests for config_loader.py: load_yaml, deep_merge, and load_config.

Covers:
- Normal:  valid YAML files load correctly; dicts merge as expected
- Edge:    empty YAML, empty base/override dicts
- Failure: missing DPG config raises FileNotFoundError (hard fail);
           missing domain config is silently ignored by load_config
"""

from __future__ import annotations

import pytest
from pathlib import Path

from config_loader import load_yaml, deep_merge, load_config


# ---------------------------------------------------------------------------
# load_yaml — normal execution
# ---------------------------------------------------------------------------

class TestLoadYamlNormal:
    def test_valid_yaml_returns_dict(self, tmp_path):
        f = tmp_path / "cfg.yaml"
        f.write_text("agent_core_client:\n  endpoint: http://localhost:8000/process_turn\n")
        result = load_yaml(str(f))
        assert result["agent_core_client"]["endpoint"] == "http://localhost:8000/process_turn"

    def test_returns_dict_type(self, tmp_path):
        f = tmp_path / "cfg.yaml"
        f.write_text("key: value\n")
        assert isinstance(load_yaml(str(f)), dict)

    def test_nested_structure_preserved(self, tmp_path):
        f = tmp_path / "cfg.yaml"
        f.write_text("a:\n  b:\n    c: 42\n")
        assert load_yaml(str(f))["a"]["b"]["c"] == 42


# ---------------------------------------------------------------------------
# load_yaml — edge cases
# ---------------------------------------------------------------------------

class TestLoadYamlEdge:
    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("")
        assert load_yaml(str(f)) == {}

    def test_yaml_with_only_whitespace_returns_empty_dict(self, tmp_path):
        f = tmp_path / "ws.yaml"
        f.write_text("   \n\n")
        assert load_yaml(str(f)) == {}


# ---------------------------------------------------------------------------
# load_yaml — failure scenarios
# ---------------------------------------------------------------------------

class TestLoadYamlFailure:
    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_yaml(str(tmp_path / "nonexistent.yaml"))

    def test_error_message_contains_path(self, tmp_path):
        path = str(tmp_path / "missing.yaml")
        with pytest.raises(FileNotFoundError, match="missing.yaml"):
            load_yaml(path)


# ---------------------------------------------------------------------------
# deep_merge — normal execution
# ---------------------------------------------------------------------------

class TestDeepMergeNormal:
    def test_override_wins_on_scalar_conflict(self):
        base = {"agent_core_client": {"timeout_s": 30.0}}
        override = {"agent_core_client": {"timeout_s": 60.0}}
        result = deep_merge(base, override)
        assert result["agent_core_client"]["timeout_s"] == 60.0

    def test_non_overlapping_keys_combined(self):
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_nested_dicts_merged_recursively(self):
        base = {"agent_core_client": {"endpoint": "http://localhost:8000", "timeout_s": 30.0}}
        override = {"agent_core_client": {"endpoint": "http://agent-core:8000"}}
        result = deep_merge(base, override)
        assert result["agent_core_client"]["endpoint"] == "http://agent-core:8000"
        assert result["agent_core_client"]["timeout_s"] == 30.0


# ---------------------------------------------------------------------------
# deep_merge — edge cases
# ---------------------------------------------------------------------------

class TestDeepMergeEdge:
    def test_empty_override_returns_base_unchanged(self):
        base = {"agent_core_client": {"timeout_s": 30.0}}
        assert deep_merge(base, {}) == base

    def test_empty_base_returns_override(self):
        assert deep_merge({}, {"x": 10}) == {"x": 10}

    def test_both_empty_returns_empty(self):
        assert deep_merge({}, {}) == {}

    def test_base_not_mutated(self):
        base = {"a": 1}
        deep_merge(base, {"a": 2})
        assert base == {"a": 1}

    def test_dpg_only_config_produces_valid_merged_result(self):
        """Bare-infra mode: deep_merge(dpg, {}) gives dpg defaults unchanged."""
        dpg = {"agent_core_client": {"endpoint": "http://localhost:8000", "timeout_s": 30.0}}
        assert deep_merge(dpg, {}) == dpg


# ---------------------------------------------------------------------------
# load_config — normal execution
# ---------------------------------------------------------------------------

class TestLoadConfigNormal:
    def test_returns_merged_dict(self, tmp_path):
        dpg = tmp_path / "dpg.yaml"
        domain = tmp_path / "domain.yaml"
        dpg.write_text("agent_core_client:\n  timeout_s: 30.0\n  endpoint: http://localhost:8000/process_turn\n")
        domain.write_text("agent_core_client:\n  endpoint: http://agent-core:8000/process_turn\n")
        result = load_config(str(dpg), str(domain))
        assert result["agent_core_client"]["timeout_s"] == 30.0
        assert result["agent_core_client"]["endpoint"] == "http://agent-core:8000/process_turn"

    def test_domain_values_override_dpg(self, tmp_path):
        dpg = tmp_path / "dpg.yaml"
        domain = tmp_path / "domain.yaml"
        dpg.write_text("server:\n  port: 8005\n")
        domain.write_text("server:\n  port: 9000\n")
        result = load_config(str(dpg), str(domain))
        assert result["server"]["port"] == 9000


# ---------------------------------------------------------------------------
# load_config — edge cases
# ---------------------------------------------------------------------------

class TestLoadConfigEdge:
    def test_missing_domain_config_falls_back_to_dpg_defaults(self, tmp_path):
        dpg = tmp_path / "dpg.yaml"
        dpg.write_text("agent_core_client:\n  timeout_s: 30.0\n")
        result = load_config(str(dpg), str(tmp_path / "domain.yaml"))
        assert result["agent_core_client"]["timeout_s"] == 30.0


# ---------------------------------------------------------------------------
# load_config — failure scenarios
# ---------------------------------------------------------------------------

class TestLoadConfigFailure:
    def test_missing_dpg_config_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "dpg.yaml"), str(tmp_path / "domain.yaml"))
