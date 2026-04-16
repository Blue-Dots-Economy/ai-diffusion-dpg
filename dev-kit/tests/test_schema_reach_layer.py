"""Tests for updated ReachLayer schema."""
import pytest
from pydantic import ValidationError
from dev_kit.schema import ReachLayerConfig, validate_partial


def test_web_channel_config_validates():
    """A valid web channel config should parse without errors."""
    data = {
        "reach_layer": {
            "common": {"observability": {"domain": "kkb"}},
            "channels": {
                "web": {
                    "auth": {"enabled": False, "google_client_id": "", "cookie_secure": False},
                    "ui": {
                        "app_name": "Kaam Ki Baat",
                        "app_tagline": "DPG Skill-Jobs AI",
                        "app_icon": "💼",
                    },
                }
            },
        }
    }
    config = ReachLayerConfig.model_validate(data)
    assert config.reach_layer.channels.web.ui["app_name"] == "Kaam Ki Baat"
    assert config.reach_layer.channels.web.auth.enabled is False


def test_cli_channel_config_validates():
    """A valid CLI channel config should parse without errors."""
    data = {
        "reach_layer": {
            "channels": {
                "cli": {"prompt": "You: ", "agent_prefix": "Agent: "}
            }
        }
    }
    config = ReachLayerConfig.model_validate(data)
    assert config.reach_layer.channels.cli.prompt == "You: "


def test_voice_channel_config_validates():
    """A valid voice channel config should parse without errors."""
    data = {
        "reach_layer": {
            "channels": {
                "voice": {
                    "raya": {"stt_language": "hi", "tts_language": "hi", "voice_id": "abc-123"},
                    "agent_core": {
                        "timeout_ms": 15000,
                        "greeting": "Namaste!",
                        "fallback_phrase": "Please repeat.",
                    },
                }
            }
        }
    }
    config = ReachLayerConfig.model_validate(data)
    assert config.reach_layer.channels.voice.raya.stt_language == "hi"


def test_multiple_channels_coexist():
    """Web and CLI channels can both be configured simultaneously."""
    data = {
        "reach_layer": {
            "channels": {
                "cli": {"prompt": "You: ", "agent_prefix": "Agent: "},
                "web": {"auth": {"enabled": False}, "ui": {"app_name": "Test App"}},
            }
        }
    }
    config = ReachLayerConfig.model_validate(data)
    assert config.reach_layer.channels.cli is not None
    assert config.reach_layer.channels.web is not None


def test_validate_partial_channels():
    """validate_partial should accept valid reach_layer partial."""
    data = {
        "reach_layer": {
            "channels": {
                "web": {"ui": {"app_name": "My App"}}
            }
        }
    }
    errors = validate_partial("reach_layer", data)
    assert errors == [], f"Unexpected errors: {errors}"
