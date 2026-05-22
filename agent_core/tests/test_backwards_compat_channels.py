"""
GH-137 backwards-compat smoke: the in-tree domain config(s) migrated to the
new top-level `channels:` path must load and instantiate without error.
"""
import yaml
from pathlib import Path
from unittest.mock import MagicMock

from src.chat_provider.base import ChatProviderBase
from src.preprocessing.nlu_processor import NLUProcessor


def _mock_provider():
    return MagicMock(spec=ChatProviderBase)


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


def test_kkb_nlu_processor_instantiates():
    cfg = _load_merged_domain_config("kkb")
    p = NLUProcessor(cfg, chat_provider=_mock_provider())
    assert p is not None
