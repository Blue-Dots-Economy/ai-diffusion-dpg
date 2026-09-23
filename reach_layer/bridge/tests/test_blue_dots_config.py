"""The blue-dots domain declares a usable bridge channel."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "agent_core" / "src"))

from schema.config import ChannelsConfig  # noqa: E402

_AC = _REPO / "dev-kit" / "configs" / "blue-dots" / "agent_core.yaml"

pytestmark = pytest.mark.skipif(
    not _AC.exists(), reason="blue-dots config not in this checkout"
)


def _channels() -> dict:
    return yaml.safe_load(_AC.read_text())["channels"]


def test_bridge_channel_exists_and_validates():
    ChannelsConfig.model_validate(_channels())
    assert "bridge" in _channels()


def test_bridge_has_a_terminal_word():
    """Spec 11.5 — the closing word is the only signal the caller gets that
    the conversation finished, since the shim does not end the call."""
    assert _channels()["bridge"]["terminal_word"].strip()


def test_bridge_prompt_rules_target_a_listener_not_a_reader():
    suffix = _channels()["bridge"]["system_prompt_suffix"].lower()
    assert "hear" in suffix or "listen" in suffix
    assert "markdown" in suffix


def test_bridge_declares_tts_rules():
    """No TTS sanitizer sits downstream here, so the model must produce
    speech-ready text itself."""
    assert _channels()["bridge"].get("tts_rules")


def test_existing_channels_are_untouched():
    channels = _channels()
    for name in ("voice", "web"):
        assert name in channels
        assert channels[name]["system_prompt_suffix"].strip()
