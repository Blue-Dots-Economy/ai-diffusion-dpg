"""
GH-137 backwards-compat smoke: the three in-tree domain configs migrated to
the new top-level `channels:` path must load and instantiate without error.
"""
import yaml
from pathlib import Path

from src.preprocessing.nlu_processor import NLUProcessor


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


def test_kkb_has_top_level_channels():
    cfg = _load_merged_domain_config("kkb")
    assert "channels" in cfg
    assert "channels" not in cfg.get("agent", {})
    assert "channels" not in cfg.get("reach_layer", {})


def test_farmer_friendly_has_no_legacy_channels():
    cfg = _load_merged_domain_config("farmer-friendly")
    assert "channels" not in cfg.get("agent", {})
    assert "channels" not in cfg.get("reach_layer", {})


def test_obsrv_docs_assistant_has_no_legacy_channels():
    cfg = _load_merged_domain_config("obsrv-docs-assistant")
    assert "channels" not in cfg.get("agent", {})
    assert "channels" not in cfg.get("reach_layer", {})


def test_kkb_nlu_processor_instantiates():
    cfg = _load_merged_domain_config("kkb")
    p = NLUProcessor(cfg)
    assert p is not None


def test_farmer_friendly_nlu_processor_instantiates():
    cfg = _load_merged_domain_config("farmer-friendly")
    p = NLUProcessor(cfg)
    assert p is not None


def test_obsrv_docs_assistant_nlu_processor_instantiates():
    cfg = _load_merged_domain_config("obsrv-docs-assistant")
    p = NLUProcessor(cfg)
    assert p is not None
