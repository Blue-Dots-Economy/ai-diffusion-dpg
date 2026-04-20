"""
GH-139 backwards-compatibility smoke.

Verifies that agent_core domain configs that DO NOT declare
conversation.user_state_model continue to start cleanly — no
ConfigurationError, no changed behaviour.
"""
import yaml
from pathlib import Path

from src.preprocessing.nlu_processor import NLUProcessor


def _load_merged_domain_config(domain: str) -> dict:
    """Mimic the deep-merge the runtime does at startup."""
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


def test_kkb_config_boots_without_user_state_model():
    """KKB domain boots cleanly; user_state disabled by default."""
    cfg = _load_merged_domain_config("kkb")
    p = NLUProcessor(cfg)
    assert p._user_state_enabled is False


def test_farmer_friendly_boots_with_example_user_state():
    """Farmer-friendly domain boots cleanly with example user_state_model."""
    cfg = _load_merged_domain_config("farmer-friendly")
    p = NLUProcessor(cfg)
    assert p._user_state_enabled is True
    assert p._user_state_default != ""
    assert len(p._user_states) >= 2


def test_obsrv_docs_assistant_boots():
    """Obsrv-docs-assistant domain boots cleanly; user_state disabled by default."""
    cfg = _load_merged_domain_config("obsrv-docs-assistant")
    p = NLUProcessor(cfg)
    assert p._user_state_enabled is False
