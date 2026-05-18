"""Tests for updated ReachLayer schema."""
import pytest
from pydantic import ValidationError
from dev_kit.schema import ReachLayerConfig
from dev_kit.schemas.validation import validate_partial


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


def test_validate_full_passes_complete_data():
    """validate_full accepts the same complete-shape data validate_partial does."""
    from dev_kit.schemas.validation import validate_full
    data = {
        "reach_layer": {
            "channels": {
                "web": {"ui": {"app_name": "My App"}}
            }
        }
    }
    errors = validate_full("reach_layer", data)
    assert errors == [], f"Unexpected errors: {errors}"


def test_validate_full_surfaces_missing_required_fields():
    """validate_full does NOT filter 'missing' errors — partial drafts that
    would silently pass validate_partial should fail validate_full."""
    from dev_kit.schemas.validation import validate_full, validate_partial
    # action_gateway 'tools' is a list[ToolDefinition]; each tool has
    # required fields (id, description, base_url). An empty list passes
    # both validators. A tool with NO required fields surfaces missing
    # errors only when omit_missing=False.
    data = {"tools": [{"id": "x"}]}  # missing description, base_url, etc.
    partial_errors = validate_partial("action_gateway", data)
    full_errors = validate_full("action_gateway", data)
    assert full_errors, "validate_full should flag missing required fields"
    assert len(full_errors) >= len(partial_errors)


def test_validate_full_unknown_block():
    """validate_full returns the same unknown-block error as validate_partial."""
    from dev_kit.schemas.validation import validate_full
    errors = validate_full("nope_layer", {"x": 1})
    assert errors and "Unknown block" in errors[0]


def test_validate_full_empty_data():
    """validate_full returns no errors for empty input (nothing to validate)."""
    from dev_kit.schemas.validation import validate_full
    assert validate_full("agent_core", {}) == []
