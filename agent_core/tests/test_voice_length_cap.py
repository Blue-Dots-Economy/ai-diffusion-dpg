"""
GH-194 — Hard response-length cap on voice channel.

Two-pronged cap:
  1. Prompt rule: voice ``system_prompt_suffix`` carries an explicit
     "at most 2 short sentences" instruction (with a market-listing
     exception), and the assembled system prompt surfaces it.
  2. Token cap: ``channels.voice.max_tokens`` flows from the merged config
     through the orchestrator into ``stream_call(max_tokens=...)``, which
     the Anthropic SDK call then honours. When the cap is unset the wrapper
     falls back to its built-in default of 4096.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from unittest.mock import MagicMock

from src.manager_agent import ManagerAgent
from src.schema.config import ChannelConfig, MergedConfig


def _flat(blocks: list[dict]) -> str:
    return "\n\n".join(b.get("text", "") for b in (blocks or []))


def _load_kkb_merged() -> dict:
    repo_root = Path(__file__).resolve().parents[2]
    dpg = yaml.safe_load((repo_root / "dev-kit" / "dpg" / "agent_core.yaml").read_text()) or {}
    dom = yaml.safe_load(
        (repo_root / "dev-kit" / "configs" / "kkb" / "agent_core.yaml").read_text()
    ) or {}
    merged: dict = {**dpg}
    for k, v in dom.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


# ---------------------------------------------------------------------------
# Prompt cap: KKB voice suffix carries the sentence-cap rule
# ---------------------------------------------------------------------------


def test_kkb_voice_suffix_contains_sentence_cap_rule():
    """KKB voice channel suffix declares the at-most-2-sentences hard rule."""
    cfg = _load_kkb_merged()
    suffix = cfg["channels"]["voice"]["system_prompt_suffix"]

    assert "at most 2 short sentences" in suffix
    assert "market listing" in suffix
    assert "at most 3 items" in suffix


def test_assembled_voice_prompt_includes_sentence_cap_rule():
    """The assembled system prompt for the voice channel surfaces the cap rule."""
    cfg = _load_kkb_merged()
    voice_cfg = cfg["channels"]["voice"]

    agent = ManagerAgent(
        llm_wrapper=MagicMock(),
        tool_registry=MagicMock(),
        action_gateway=MagicMock(),
        knowledge_engine=MagicMock(),
        trust_layer=MagicMock(),
    )
    blocks = agent.build_system_prompt(
        agent_system_prompt="You are काम की बात.",
        subagent_system_prompt="Help with jobs.",
        detected_language="hindi",
        channel="voice",
        profile={},
        channel_config=voice_cfg,
    )
    rendered = _flat(blocks)

    assert "at most 2 short sentences" in rendered
    assert "<channel_rules>" in rendered


# ---------------------------------------------------------------------------
# Token cap: ChannelConfig schema accepts and exposes max_tokens
# ---------------------------------------------------------------------------


def test_channel_config_schema_accepts_max_tokens():
    """ChannelConfig (Pydantic) accepts max_tokens and exposes it."""
    cc = ChannelConfig.model_validate({"max_tokens": 200})
    assert cc.max_tokens == 200


def test_channel_config_default_max_tokens_is_none():
    """When not set, max_tokens defaults to None so the wrapper uses its own default."""
    cc = ChannelConfig()
    assert cc.max_tokens is None


def test_channel_config_rejects_non_positive_max_tokens():
    """max_tokens must be > 0 — guards against accidental zero/negative caps."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ChannelConfig.model_validate({"max_tokens": 0})


def test_kkb_voice_channel_max_tokens_validates_against_schema():
    """KKB merged config validates fully against the strict MergedConfig schema."""
    cfg = _load_kkb_merged()
    merged = MergedConfig.validate_full(cfg)
    assert merged.channels.voice.max_tokens == 200
    # Non-voice channels intentionally leave max_tokens unset (default cap applies).
    assert merged.channels.web.max_tokens is None
    assert merged.channels.cli.max_tokens is None
