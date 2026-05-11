"""Tests for build_recording_manager factory."""
from __future__ import annotations

import pytest

from src.recordings.factory import build_recording_manager
from src.recordings.manager import NullRecordingManager, RecordingManager


def _cfg(source="disabled", backend="local", salt="", bucket=""):
    return {
        "reach_layer": {"channels": {"voice": {
            "vobiz": {"auth_id": "A", "auth_token": "T", "sample_rate": 8000},
            "recording": {
                "source": source,
                "consent_purpose": "recording",
                "webhook_timeout_s": 5.0, "fetch_timeout_s": 5.0, "min_duration_ms": 10,
                "caller_id_hash_salt": salt,
                "store": {
                    "backend": backend,
                    "local": {"base_path": "/tmp/r"},
                    "s3": {"bucket": bucket, "prefix": "rec/", "region": "ap-south-1", "kms_key_id": ""},
                },
            },
        }}},
    }


def test_disabled_returns_null():
    m = build_recording_manager(_cfg(), telephony=None, registry={})
    assert isinstance(m, NullRecordingManager)


def test_pipeline_local_returns_real_manager():
    m = build_recording_manager(_cfg(source="pipeline", salt="s" * 32), telephony=None, registry={})
    assert isinstance(m, RecordingManager)
    assert m.pipeline_processors  # has the tap processor


def test_vobiz_s3_returns_real_manager():
    m = build_recording_manager(
        _cfg(source="vobiz", backend="s3", salt="s" * 32, bucket="b"),
        telephony=None, registry={},
    )
    assert isinstance(m, RecordingManager)


def test_enabled_without_salt_raises():
    with pytest.raises(ValueError, match="caller_id_hash_salt"):
        build_recording_manager(_cfg(source="vobiz"), telephony=None, registry={})


def test_s3_without_bucket_raises():
    with pytest.raises(ValueError, match="bucket"):
        build_recording_manager(
            _cfg(source="vobiz", backend="s3", salt="s" * 32, bucket=""),
            telephony=None, registry={},
        )


def test_unknown_source_raises():
    cfg = _cfg()
    cfg["reach_layer"]["channels"]["voice"]["recording"]["source"] = "ftp"
    with pytest.raises(ValueError, match="recording.source"):
        build_recording_manager(cfg, telephony=None, registry={})


def test_unknown_backend_raises():
    cfg = _cfg(source="vobiz", salt="s" * 32)
    cfg["reach_layer"]["channels"]["voice"]["recording"]["store"]["backend"] = "azure"
    with pytest.raises(ValueError, match="backend"):
        build_recording_manager(cfg, telephony=None, registry={})


def test_caller_id_hash_is_truncated_sha256():
    import hashlib
    m = build_recording_manager(
        _cfg(source="pipeline", salt="abc"),
        telephony=None, registry={},
        caller_id="+910000000001",
    )
    expected = hashlib.sha256(b"abc+910000000001").hexdigest()[:16]
    assert m._caller_id_hash == expected  # noqa: SLF001 — internal check is intentional
