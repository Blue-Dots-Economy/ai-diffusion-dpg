"""Tests that MergedConfig accepts the new recording block."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from reach_layer_base.schema.config import (
    MergedConfig,
    RecordingConfig,
    VoiceChannelConfig,
)


def test_recording_defaults_to_disabled():
    rec = RecordingConfig()
    assert rec.source == "disabled"
    assert rec.store.backend == "local"


def test_recording_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        RecordingConfig(surprise="x")  # type: ignore[call-arg]


def test_voice_channel_config_accepts_recording_block():
    voice = VoiceChannelConfig(recording={
        "source": "disabled",
        "consent_purpose": "recording",
        "webhook_timeout_s": 30,
        "fetch_timeout_s": 60,
        "min_duration_ms": 500,
        "caller_id_hash_salt": "",
        "store": {
            "backend": "local",
            "local": {"base_path": "/var/recordings"},
            "s3": {"bucket": "", "prefix": "recordings/", "region": "ap-south-1", "kms_key_id": ""},
        },
    })
    assert voice.recording.source == "disabled"


def test_merged_config_accepts_recording_in_yaml_shape():
    """The shape coming out of load_reach_config must validate."""
    merged_min: dict = {
        "reach_layer": {
            "channels": {
                "voice": {
                    "recording": {
                        "source": "disabled",
                        "consent_purpose": "recording",
                        "webhook_timeout_s": 30,
                        "fetch_timeout_s": 60,
                        "min_duration_ms": 500,
                        "caller_id_hash_salt": "",
                        "store": {
                            "backend": "local",
                            "local": {"base_path": "/var/recordings"},
                            "s3": {"bucket": "", "prefix": "recordings/", "region": "ap-south-1", "kms_key_id": ""},
                        },
                    },
                },
            },
        },
    }
    cfg = MergedConfig.model_validate(merged_min)
    assert cfg.reach_layer.channels.voice.recording.source == "disabled"
