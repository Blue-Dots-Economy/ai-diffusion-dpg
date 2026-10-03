"""The Blue Dots config declares every channel Reach Layer can send: web, cli, voice and bridge."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.nlu.offline import load_merged_config
from src.orchestrator import AgentCore
from src.output.contract import render_output_contract
from src.schema.config import MergedConfig

BD = Path(__file__).resolve().parents[2] / "dev-kit" / "configs" / "blue-dots"
CHANNELS = ("web", "cli", "voice", "bridge")


@pytest.fixture(scope="module")
def cfg() -> dict:
    merged = load_merged_config(BD)
    MergedConfig.validate_full(merged)
    return merged


@pytest.mark.parametrize("channel", CHANNELS)
def test_channel_resolves(cfg, channel):
    """No 'Unsupported channel' for any channel the stack serves (PR #419 dropped web/cli/voice)."""
    resolved = AgentCore._resolve_channel_config(SimpleNamespace(_config=cfg), channel)
    assert resolved["system_prompt_suffix"].strip()
    assert render_output_contract(resolved.get("output_contract"))
    assert resolved["output_contract"]["default_language"] == "hindi"


@pytest.mark.parametrize("channel", ("web", "cli"))
def test_text_channels_write_digits_for_a_reader(cfg, channel):
    ch = cfg["channels"][channel]
    assert "READS" in ch["system_prompt_suffix"]
    assert {e["numbers"] for e in ch["output_contract"]["languages"].values()} == {"digits"}
    assert not (ch["output_contract"].get("guard") or {}).get("rewrite_digits")


@pytest.mark.parametrize("channel", ("voice", "bridge"))
def test_spoken_channels_speak_words_for_a_listener(cfg, channel):
    ch = cfg["channels"][channel]
    assert "HEAR" in ch["system_prompt_suffix"]
    assert ch["output_contract"]["languages"]["hindi"]["numbers"] == "words"
    assert ch["output_contract"]["guard"]["rewrite_digits"] is True
    assert ch["terminal_word"].strip()


@pytest.mark.parametrize("channel", CHANNELS)
def test_channels_share_the_language_rule(cfg, channel):
    """Hindi by default, English on switch; Gujarati was dropped from the domain."""
    suffix = cfg["channels"][channel]["system_prompt_suffix"]
    assert "Hindi by" in suffix and "Switch to English only if" in suffix
    assert "Gujarati" not in suffix
