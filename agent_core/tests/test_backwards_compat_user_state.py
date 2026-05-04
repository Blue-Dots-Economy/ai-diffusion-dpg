"""
GH-139 backwards-compatibility smoke.

Verifies that agent_core domain configs that DO NOT declare
conversation.user_state_model continue to start cleanly — no
ConfigurationError, no changed behaviour.
"""
import yaml
from pathlib import Path
from unittest.mock import MagicMock

from src.chat_provider.base import ChatProviderBase
from src.preprocessing.nlu_processor import NLUProcessor


def _mock_provider():
    return MagicMock(spec=ChatProviderBase)


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


def test_kkb_config_boots_with_user_state_model_enabled():
    """KKB domain boots cleanly with user_state_model enabled (GH-139)."""
    cfg = _load_merged_domain_config("kkb")
    p = NLUProcessor(cfg, chat_provider=_mock_provider())
    assert p._user_state_enabled is True


def test_obsrv_docs_assistant_boots():
    """Obsrv-docs-assistant domain boots cleanly; user_state disabled by default."""
    cfg = _load_merged_domain_config("obsrv-docs-assistant")
    p = NLUProcessor(cfg, chat_provider=_mock_provider())
    assert p._user_state_enabled is False
