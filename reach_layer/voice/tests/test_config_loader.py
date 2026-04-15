# telephony_adapter/tests/test_config_loader.py
import pytest
from pathlib import Path
from config_loader import load_config, load_yaml, deep_merge


def _write_yaml(path: Path, content: str):
    path.write_text(content)


def test_deep_merge_overrides_scalar():
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99}}
    result = deep_merge(base, override)
    assert result["b"]["c"] == 99
    assert result["b"]["d"] == 3


def test_deep_merge_does_not_mutate_base():
    base = {"a": {"b": 1}}
    deep_merge(base, {"a": {"b": 2}})
    assert base["a"]["b"] == 1


def test_load_config_merges_domain_over_defaults(tmp_path):
    dpg = tmp_path / "dpg.yaml"
    domain = tmp_path / "domain.yaml"
    _write_yaml(dpg, "telephony_adapter:\n  port: 8006\n  language: en\n")
    _write_yaml(domain, "telephony_adapter:\n  language: hi\n")
    cfg = load_config(str(dpg), str(domain))
    assert cfg["telephony_adapter"]["port"] == 8006
    assert cfg["telephony_adapter"]["language"] == "hi"


def test_load_config_missing_domain_uses_defaults(tmp_path):
    dpg = tmp_path / "dpg.yaml"
    _write_yaml(dpg, "telephony_adapter:\n  port: 8006\n")
    cfg = load_config(str(dpg), str(tmp_path / "missing.yaml"))
    assert cfg["telephony_adapter"]["port"] == 8006


def test_load_yaml_raises_on_missing_file():
    with pytest.raises(FileNotFoundError):
        load_yaml("/nonexistent/path/config.yaml")
