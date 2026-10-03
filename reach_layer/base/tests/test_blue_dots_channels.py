"""The Blue Dots Reach Layer config loads for every channel; the web chat runs without login."""
from __future__ import annotations

from pathlib import Path

import pytest

from reach_layer_base.config_loader import load_reach_config

_REPO = Path(__file__).resolve().parents[3]
_DPG = _REPO / "dev-kit" / "dpg" / "reach_layer.yaml"
_DOMAIN = _REPO / "dev-kit" / "configs" / "blue-dots" / "reach_layer.yaml"


def _load(channel: str) -> dict:
    return load_reach_config(channel, dpg_path=str(_DPG), domain_path=str(_DOMAIN))


@pytest.mark.parametrize("channel", ["web", "cli", "voice", "bridge"])
def test_channel_loads(channel):
    cfg = _load(channel)
    assert cfg["reach_layer"]["channels"][channel]["enabled"] is True


def test_web_auth_disabled_without_google_or_session_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("REACH_SESSION_SECRET", raising=False)
    cfg = _load("web")
    assert cfg["reach_layer"]["channels"]["web"]["auth"]["enabled"] is False
    assert cfg["auth"]["enabled"] is False  # the alias web/server.py reads
    assert cfg["ui"]["app_name"] == "Blue Dots"


def test_voice_speaks_hindi():
    raya = _load("voice")["reach_layer"]["channels"]["voice"]["raya"]
    assert raya["stt_language"] == raya["tts_language"] == "hi"
