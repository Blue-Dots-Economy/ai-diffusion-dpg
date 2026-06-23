"""Tests for Reach Layer MergedConfig strict schema validation.

The schema lives in the shared ``base`` package and is exercised by the
CLI channel's test suite as a convenient harness — the schema applies
identically to web and voice channels.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

# Make the base/ package importable even when running from cli/.
_BASE_DIR = Path(__file__).resolve().parents[2] / "base"
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from schema.config import (  # noqa: E402
    AssemblyMode,
    CookieSameSite,
    MergedConfig,
    WebServerConfig,
)


def _minimal_valid_config() -> dict:
    return {
        "reach_layer": {
            "common": {
                "agent_core_client": {
                    "endpoint": "http://agent_core:8000/process_turn",
                    "timeout_s": 30.0,
                },
                "memory_layer_client": {
                    "endpoint": "http://memory_layer:8002",
                    "timeout_s": 10.0,
                },
                "observability": {"domain": "kkb"},
            },
            "channels": {
                "cli": {
                    "enabled": True,
                    "assembly_mode": "session",
                    "prompt": "You: ",
                    "agent_prefix": "Agent: ",
                },
                "web": {
                    "enabled": True,
                    "assembly_mode": "session",
                    "server": {"host": "0.0.0.0", "port": 8005},
                    "sessions": {"limit": 25},
                    "auth": {
                        "enabled": False,
                        "google_client_id": "",
                        "cookie_secure": False,
                        "session_cookie_name": "reach_session",
                        "session_ttl_s": 86400,
                        "cookie_samesite": "lax",
                    },
                    "ui": {"app_name": "Kaam Ki Baat", "app_icon": "💼"},
                },
                "voice": {
                    "enabled": True,
                    "assembly_mode": "session",
                    "port": 8006,
                    "public_url": "https://example.com",
                    "vobiz": {"auth_id": "x", "auth_token": "y"},
                    "vad": {"stop_secs": 0.4, "min_volume": 0.7, "confidence": 0.75},
                    "raya": {
                        "api_key": "x",
                        "stt_language": "hi",
                        "tts_language": "hi",
                        "voice_id": "v1",
                    },
                    "agent_core": {
                        "base_url": "http://agent_core:8000",
                        "timeout_ms": 15000,
                        "fallback_phrase": "Sorry, please repeat.",
                        "barge_in_acknowledgement": "Ok one sec.",
                    },
                },
            },
        }
    }


def test_accepts_valid_full_config():
    cfg = MergedConfig.validate_full(_minimal_valid_config())
    assert cfg.reach_layer.common.observability.domain == "kkb"
    assert cfg.reach_layer.channels.cli.assembly_mode == AssemblyMode.session
    assert cfg.reach_layer.channels.web.server.port == 8005
    assert cfg.reach_layer.channels.web.auth.cookie_samesite == CookieSameSite.lax
    assert cfg.reach_layer.channels.voice.vad.confidence == 0.75
    assert cfg.reach_layer.channels.voice.agent_core.barge_in_acknowledgement == "Ok one sec."


def test_accepts_empty_config_with_defaults():
    cfg = MergedConfig.validate_full({
        "reach_layer": {
            "channels": {
                "cli": {},
                "web": {},
                "voice": {},
            }
        }
    })
    assert cfg.reach_layer.channels.cli.enabled is True
    assert cfg.reach_layer.channels.web.server.port == 8005
    assert cfg.reach_layer.channels.voice.port == 8006


def test_rejects_removed_voice_greeting_field():
    """`greeting` on voice.agent_core was removed in this PR — per-call welcome
    now comes from agent_core opening_phrase (GH-149)."""
    config = _minimal_valid_config()
    config["reach_layer"]["channels"]["voice"]["agent_core"]["greeting"] = "Namaste"
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "greeting" in str(exc.value)


def test_rejects_unknown_top_level_key():
    config = _minimal_valid_config()
    config["typo_section"] = {}
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "typo_section" in str(exc.value)


def test_rejects_unknown_key_on_common():
    config = _minimal_valid_config()
    config["reach_layer"]["common"]["trust_client"] = {"endpoint": "x"}  # not a reach concern
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "trust_client" in str(exc.value)


def test_rejects_unknown_channel_name():
    config = _minimal_valid_config()
    config["reach_layer"]["channels"]["whatsapp"] = {"enabled": True}
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "whatsapp" in str(exc.value)


def test_rejects_unknown_key_on_web_auth():
    config = _minimal_valid_config()
    config["reach_layer"]["channels"]["web"]["auth"]["signing_key"] = "xxx"
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "signing_key" in str(exc.value)


def test_rejects_unknown_key_on_voice_vad():
    config = _minimal_valid_config()
    config["reach_layer"]["channels"]["voice"]["vad"]["threshold"] = 0.5
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "threshold" in str(exc.value)


def test_rejects_unknown_key_on_voice_raya():
    config = _minimal_valid_config()
    config["reach_layer"]["channels"]["voice"]["raya"]["model_id"] = "x"  # should be tts_model
    with pytest.raises(ValidationError) as exc:
        MergedConfig.validate_full(config)
    assert "model_id" in str(exc.value)


def test_rejects_invalid_assembly_mode_enum():
    config = _minimal_valid_config()
    config["reach_layer"]["channels"]["cli"]["assembly_mode"] = "async"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_invalid_cookie_samesite_enum():
    config = _minimal_valid_config()
    config["reach_layer"]["channels"]["web"]["auth"]["cookie_samesite"] = "permissive"
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_out_of_range_vad_confidence():
    config = _minimal_valid_config()
    config["reach_layer"]["channels"]["voice"]["vad"]["confidence"] = 1.5
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_non_positive_port():
    with pytest.raises(ValidationError):
        WebServerConfig(port=0)
    with pytest.raises(ValidationError):
        WebServerConfig(port=70000)


def test_rejects_non_positive_session_ttl():
    config = _minimal_valid_config()
    config["reach_layer"]["channels"]["web"]["auth"]["session_ttl_s"] = 0
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_non_positive_sessions_limit():
    config = _minimal_valid_config()
    config["reach_layer"]["channels"]["web"]["sessions"]["limit"] = 0
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_out_of_range_sample_rate():
    config = _minimal_valid_config()
    config["reach_layer"]["common"]["observability"] = {"otel": {"sample_rate": 2.0}}
    with pytest.raises(ValidationError):
        MergedConfig.validate_full(config)


def test_rejects_none_input():
    with pytest.raises(TypeError):
        MergedConfig.validate_full(None)


def test_real_merged_dpg_and_kkb_validates():
    """End-to-end: load the actual dev-kit yamls, merge, and validate."""
    import yaml
    repo_root = Path(__file__).resolve().parents[3]
    dpg = yaml.safe_load((repo_root / "dev-kit/dpg/reach_layer.yaml").read_text()) or {}
    kkb = yaml.safe_load((repo_root / "dev-kit/configs/kkb/reach_layer.yaml").read_text()) or {}

    def _merge(a, b):
        r = a.copy()
        for k, v in b.items():
            if k in r and isinstance(r[k], dict) and isinstance(v, dict):
                r[k] = _merge(r[k], v)
            else:
                r[k] = v
        return r

    cfg = MergedConfig.validate_full(_merge(dpg, kkb))
    assert cfg.reach_layer.channels.cli.prompt == "You: "
    assert cfg.reach_layer.channels.web.ui.app_name == "Kaam Ki Baat"
    assert cfg.reach_layer.channels.voice.raya.stt_language == "hi"


def test_enum_exports_are_usable():
    assert AssemblyMode.session.value == "session"
    assert AssemblyMode.direct.value == "direct"
    assert CookieSameSite.lax.value == "lax"
