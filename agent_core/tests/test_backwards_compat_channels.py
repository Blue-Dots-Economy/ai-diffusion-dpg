"""
GH-137 backwards-compat smoke: the in-tree domain config(s) migrated to the
new top-level `channels:` path must load without error.
"""
import yaml
from pathlib import Path


def _load_merged_domain_config(domain: str) -> dict:
    repo_root = Path(__file__).resolve().parents[2]
    dpg = yaml.safe_load((repo_root / "dev-kit" / "dpg" / "agent_core.yaml").read_text()) or {}
    dom = yaml.safe_load(
        (repo_root / "dev-kit" / "configs" / domain / "agent_core.yaml").read_text()
    ) or {}
    merged = {**dpg}
    for k, v in dom.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def test_blue_dots_has_top_level_channels():
    cfg = _load_merged_domain_config("blue-dots")
    assert "channels" in cfg
    assert "channels" not in cfg.get("agent", {})
    assert "channels" not in cfg.get("reach_layer", {})

