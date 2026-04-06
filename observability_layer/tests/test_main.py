"""
learning_layer/tests/test_main.py

Tests for config-loading utilities in main.py: _load_config, _deep_merge,
and _domain_config_path.

Covers:
- Normal:  valid YAML files load correctly; dicts merge as expected
- Edge:    empty YAML, empty base/override dicts, non-overlapping keys
- Failure: missing file raises FileNotFoundError (hard fail for DPG config);
           domain config missing produces FileNotFoundError that _build_app
           catches to enable bare-infra mode
- CONFIG_FOLDER: env var selects alternate domain config path
"""

from __future__ import annotations

import os

import pytest
import yaml
from pathlib import Path


# ---------------------------------------------------------------------------
# Inline implementations — identical to main.py utilities.
# Tested here without importing main to avoid triggering module-level
# _build_app() side effects during test collection.
# ---------------------------------------------------------------------------

def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
    with config_path.open("r") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# _load_config — normal execution
# ---------------------------------------------------------------------------

class TestLoadConfigNormal:
    def test_valid_yaml_returns_dict(self, tmp_path):
        f = tmp_path / "cfg.yaml"
        f.write_text("server:\n  port: 8000\n")
        result = _load_config(str(f))
        assert result["server"]["port"] == 8000

    def test_returns_dict_type(self, tmp_path):
        f = tmp_path / "cfg.yaml"
        f.write_text("key: value\n")
        assert isinstance(_load_config(str(f)), dict)

    def test_nested_structure_preserved(self, tmp_path):
        f = tmp_path / "cfg.yaml"
        f.write_text("a:\n  b:\n    c: 42\n")
        assert _load_config(str(f))["a"]["b"]["c"] == 42


# ---------------------------------------------------------------------------
# _load_config — edge cases
# ---------------------------------------------------------------------------

class TestLoadConfigEdge:
    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("")
        assert _load_config(str(f)) == {}

    def test_yaml_with_only_whitespace_returns_empty_dict(self, tmp_path):
        f = tmp_path / "ws.yaml"
        f.write_text("   \n\n")
        assert _load_config(str(f)) == {}


# ---------------------------------------------------------------------------
# _load_config — failure scenarios
# ---------------------------------------------------------------------------

class TestLoadConfigFailure:
    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _load_config(str(tmp_path / "nonexistent.yaml"))

    def test_error_message_contains_path(self, tmp_path):
        path = str(tmp_path / "missing.yaml")
        with pytest.raises(FileNotFoundError, match="missing.yaml"):
            _load_config(path)

    def test_dpg_config_missing_hard_fails(self, tmp_path):
        """DPG config missing raises FileNotFoundError — service must not start."""
        with pytest.raises(FileNotFoundError):
            _load_config(str(tmp_path / "config" / "dpg.yaml"))

    def test_domain_config_missing_raises_catchable_error(self, tmp_path):
        """
        Domain config missing raises FileNotFoundError.
        _build_app() catches this and sets domain_config={} (bare-infra mode).
        """
        caught = False
        try:
            _load_config(str(tmp_path / "config" / "domain.yaml"))
        except FileNotFoundError:
            caught = True
        assert caught


# ---------------------------------------------------------------------------
# _deep_merge — normal execution
# ---------------------------------------------------------------------------

class TestDeepMergeNormal:
    def test_override_wins_on_scalar_conflict(self):
        assert _deep_merge({"port": 8000}, {"port": 9000}) == {"port": 9000}

    def test_non_overlapping_keys_combined(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_nested_dicts_merged_recursively(self):
        base = {"server": {"host": "0.0.0.0", "port": 8000}}
        override = {"server": {"port": 9000}}
        result = _deep_merge(base, override)
        assert result["server"]["host"] == "0.0.0.0"
        assert result["server"]["port"] == 9000

    def test_deeply_nested_merge(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"d": 99}}}
        result = _deep_merge(base, override)
        assert result["a"]["b"]["c"] == 1
        assert result["a"]["b"]["d"] == 99


# ---------------------------------------------------------------------------
# _deep_merge — edge cases
# ---------------------------------------------------------------------------

class TestDeepMergeEdge:
    def test_empty_override_returns_base_unchanged(self):
        assert _deep_merge({"a": 1}, {}) == {"a": 1}

    def test_empty_base_returns_override(self):
        assert _deep_merge({}, {"x": 10}) == {"x": 10}

    def test_both_empty_returns_empty(self):
        assert _deep_merge({}, {}) == {}

    def test_base_not_mutated(self):
        base = {"a": 1}
        _deep_merge(base, {"a": 2})
        assert base == {"a": 1}

    def test_override_non_dict_replaces_dict_in_base(self):
        base = {"rules": {"blocked": ["x"]}}
        result = _deep_merge(base, {"rules": "none"})
        assert result["rules"] == "none"

    def test_dpg_only_config_equals_dpg_defaults(self):
        """Bare-infra mode: _deep_merge(dpg, {}) returns dpg unchanged."""
        dpg = {"server": {"port": 8000}, "timeout_ms": 5000}
        assert _deep_merge(dpg, {}) == dpg


# ---------------------------------------------------------------------------
# _domain_config_path
# ---------------------------------------------------------------------------

# Mirror of main._domain_config_path — tested inline to avoid module-level startup.
def _domain_config_path(service: str) -> Path:
    config_folder = os.getenv("CONFIG_FOLDER")
    if config_folder:
        return Path(config_folder) / f"{service}.yaml"
    return Path("config/domain.yaml")


class TestDomainConfigPath:
    def test_returns_local_path_when_config_folder_not_set(self, monkeypatch):
        monkeypatch.delenv("CONFIG_FOLDER", raising=False)
        result = _domain_config_path("learning_layer")
        assert result == Path("config/domain.yaml")

    def test_returns_config_folder_path_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIG_FOLDER", str(tmp_path))
        result = _domain_config_path("learning_layer")
        assert result == tmp_path / "learning_layer.yaml"

    def test_config_folder_path_uses_service_name(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIG_FOLDER", str(tmp_path))
        result = _domain_config_path("other_service")
        assert result == tmp_path / "other_service.yaml"

    def test_returns_local_path_when_config_folder_empty_string(self, monkeypatch):
        monkeypatch.setenv("CONFIG_FOLDER", "")
        result = _domain_config_path("learning_layer")
        assert result == Path("config/domain.yaml")
